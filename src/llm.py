"""Provider-agnostic LLM access with schema validation, caching and a budget.

Written against the standard HTTP APIs rather than a vendor SDK, so it has no
dependency beyond the standard library and can fail over between providers
during the final run if one rate-limits.

Four properties matter more than features here:

* **Structured or nothing.** Callers pass a JSON Schema and get back a validated
  object. A model that returns prose gets retried, and after that the caller
  falls back to the deterministic path rather than parsing free text.
* **Cached.** Responses are keyed by prompt, model and parameters, so
  development iterations and crash recovery do not re-burn tokens.
* **Bounded.** A spend ceiling is enforced. On exhaustion the system does not
  stop; it raises :class:`BudgetExhaustedError` for the caller to degrade on,
  because a missing forecast scores the maximum penalty.
* **Silent about secrets.** The key is read from the environment, never logged,
  never written to an artifact.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .config import PATHS, Settings
from .errors import BudgetExhaustedError, ForecastSystemError
from .runlog import RunLogger

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# Rough per-million-token prices, used only to enforce the ceiling and to show a
# cost in the dashboard. Deliberately pessimistic: over-estimating spend makes
# the budget guard cautious, which is the safe direction.
DEFAULT_PRICE_PER_MTOK = (3.0, 12.0)


class LLMError(ForecastSystemError):
    """Raised when a model call cannot be completed."""


class SchemaError(LLMError):
    """Raised when a model's output does not match the requested schema."""


@dataclass
class Usage:
    """Token and cost accounting for one run."""

    calls: int = 0
    cached: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    failures: int = 0
    by_model: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "cache_hits": self.cached,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_usd": round(self.usd, 4),
            "failures": self.failures,
            "by_model": dict(self.by_model),
        }


def _post(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def available_openai_models(timeout: float = 30.0) -> list[str]:
    """List model ids the configured key can actually use.

    Model names change often enough that hardcoding one is a way to discover at
    17:20 that it was retired. Ask the account instead.
    """
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise LLMError("OPENAI_API_KEY is not set")
    request = urllib.request.Request(
        OPENAI_MODELS_URL, headers={"Authorization": f"Bearer {key}"}, method="GET"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return sorted(item["id"] for item in payload.get("data", []))


class LLM:
    """A small, cached, budgeted client over one or two providers."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        logger: RunLogger | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.logger = logger
        self.cache_dir = cache_dir or (PATHS.cache / "llm")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.usage = Usage()

    # -- availability ------------------------------------------------------

    def providers(self) -> list[str]:
        """Providers with a usable credential, primary first."""
        order = [self.settings.llm_primary]
        if self.settings.llm_fallback:
            order.append(self.settings.llm_fallback)
        keys = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
        return [
            name
            for name in order
            if name in keys and os.environ.get(keys[name], "").strip()
        ]

    @property
    def enabled(self) -> bool:
        return bool(self.providers())

    def model_for(self, tier: str) -> str:
        model = (
            self.settings.model_reasoning if tier == "reasoning" else self.settings.model_fast
        )
        if not model:
            raise LLMError(
                f"No model configured for the {tier!r} tier. "
                "Set MODEL_FAST and MODEL_REASONING in .env "
                "(run scripts/check_llm.py to see what the key can use)."
            )
        return model

    # -- caching -----------------------------------------------------------

    def _cache_key(self, model: str, system: str, prompt: str, schema: dict[str, Any] | None) -> str:
        digest = hashlib.sha256()
        for part in (model, system, prompt, json.dumps(schema, sort_keys=True) if schema else ""):
            digest.update(part.encode("utf-8"))
            digest.update(b"\x00")
        return digest.hexdigest()[:40]

    def _read_cache(self, key: str) -> Any | None:
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))["result"]
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def _write_cache(self, key: str, model: str, result: Any) -> None:
        try:
            (self.cache_dir / f"{key}.json").write_text(
                json.dumps({"model": model, "result": result}, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except OSError:
            pass

    # -- budget ------------------------------------------------------------

    def _charge(self, model: str, input_tokens: int, output_tokens: int) -> None:
        price_in, price_out = DEFAULT_PRICE_PER_MTOK
        cost = (input_tokens * price_in + output_tokens * price_out) / 1_000_000
        self.usage.calls += 1
        self.usage.input_tokens += input_tokens
        self.usage.output_tokens += output_tokens
        self.usage.usd += cost
        self.usage.by_model[model] = self.usage.by_model.get(model, 0) + 1

    def _check_budget(self) -> None:
        if self.usage.usd >= self.settings.max_usd_budget:
            raise BudgetExhaustedError(
                f"spend ceiling of ${self.settings.max_usd_budget:.2f} reached "
                f"after {self.usage.calls} calls"
            )

    # -- the call ----------------------------------------------------------

    def complete(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        system: str = "",
        tier: str = "fast",
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> dict[str, Any]:
        """Return a schema-validated JSON object from the model.

        Tries the primary provider, then the fallback. Retries with backoff on
        transient failures and on schema mismatches, since a model that returned
        prose once will usually comply when told so explicitly.
        """
        if not self.enabled:
            raise LLMError("No provider credentials are configured")

        model = self.model_for(tier)
        key = self._cache_key(model, system, prompt, schema)
        cached = self._read_cache(key)
        if cached is not None:
            self.usage.cached += 1
            return cached

        self._check_budget()
        last_error: Exception | None = None

        for provider in self.providers():
            for attempt in range(self.settings.max_retries):
                try:
                    text, usage = self._call(
                        provider, model, system, prompt, schema, temperature, max_tokens
                    )
                    self._charge(model, *usage)
                    result = _parse_json(text)
                    _validate(result, schema)
                    self._write_cache(key, model, result)
                    if self.logger:
                        self.logger.event(
                            "llm.call",
                            provider=provider,
                            model=model,
                            tier=tier,
                            input_tokens=usage[0],
                            output_tokens=usage[1],
                            attempt=attempt + 1,
                        )
                    return result
                except BudgetExhaustedError:
                    raise
                except Exception as exc:  # noqa: BLE001 - retried, then failed over
                    last_error = exc
                    self.usage.failures += 1
                    if self.logger:
                        self.logger.warning(
                            f"llm attempt failed: {exc}",
                            provider=provider,
                            model=model,
                            attempt=attempt + 1,
                        )
                    time.sleep(min(2 ** attempt, 8))

        raise LLMError(f"All providers failed: {last_error}")

    def _call(
        self,
        provider: str,
        model: str,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, tuple[int, int]]:
        instruction = (
            f"{system}\n\nReply with a single JSON object matching this schema. "
            f"No prose, no code fence.\n{json.dumps(schema)}"
        ).strip()

        if provider == "openai":
            key = os.environ["OPENAI_API_KEY"].strip()
            payload: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": instruction},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "max_completion_tokens": max_tokens,
            }
            if temperature:
                payload["temperature"] = temperature
            data = _post(
                OPENAI_URL,
                {"Authorization": f"Bearer {key}"},
                payload,
                self.settings.request_timeout_s,
            )
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            return text, (usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))

        if provider == "anthropic":
            key = os.environ["ANTHROPIC_API_KEY"].strip()
            data = _post(
                ANTHROPIC_URL,
                {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION},
                {
                    "model": model,
                    "system": instruction,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                self.settings.request_timeout_s,
            )
            text = "".join(
                block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
            )
            usage = data.get("usage", {})
            return text, (usage.get("input_tokens", 0), usage.get("output_tokens", 0))

        raise LLMError(f"Unknown provider: {provider}")


def _parse_json(text: str) -> Any:
    """Parse a model reply, tolerating a code fence or surrounding prose."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError as exc:
                raise SchemaError(f"reply was not JSON: {exc}") from exc
        raise SchemaError("reply contained no JSON object")


def _validate(value: Any, schema: dict[str, Any]) -> None:
    """Check a value against the subset of JSON Schema this system uses."""
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise SchemaError(f"expected an object, got {type(value).__name__}")
        for name in schema.get("required", []):
            if name not in value:
                raise SchemaError(f"missing required field {name!r}")
        for name, subschema in (schema.get("properties") or {}).items():
            if name in value and value[name] is not None:
                _validate(value[name], subschema)
    elif expected == "array":
        if not isinstance(value, list):
            raise SchemaError(f"expected an array, got {type(value).__name__}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for item in value:
                _validate(item, item_schema)
    elif expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SchemaError(f"expected a number, got {value!r}")
    elif expected == "string":
        if not isinstance(value, str):
            raise SchemaError(f"expected a string, got {type(value).__name__}")
    elif expected == "boolean":
        if not isinstance(value, bool):
            raise SchemaError(f"expected a boolean, got {type(value).__name__}")
