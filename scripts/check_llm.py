#!/usr/bin/env python3
"""Check which models the configured key can use, and make one test call.

Model names change often enough that hardcoding one is a way to discover during
the final run that it was retired. This asks the account instead, suggests a
fast and a reasoning tier from what is actually available, and proves a
structured call works end to end.

    python scripts/check_llm.py

Prints no credential values.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import Settings, load_dotenv  # noqa: E402
from src.llm import LLM, LLMError, available_openai_models  # noqa: E402

# Families worth routing to, most capable first within each tier. Matched as
# prefixes so a dated snapshot such as "gpt-5-2026-04-01" still matches.
REASONING_PREFERENCE = ("o4", "o3", "gpt-5", "gpt-4.1", "gpt-4o")
FAST_PREFERENCE = ("gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini", "o4-mini", "gpt-4.1", "gpt-4o")

EXCLUDE = ("audio", "realtime", "transcribe", "tts", "image", "embedding", "moderation", "search")


def pick(models: list[str], preferences: tuple[str, ...]) -> str | None:
    usable = [m for m in models if not any(word in m for word in EXCLUDE)]
    for prefix in preferences:
        matches = sorted(m for m in usable if m.startswith(prefix))
        if matches:
            # Prefer the plain family name over a dated snapshot.
            exact = [m for m in matches if m == prefix]
            return exact[0] if exact else matches[0]
    return None


def main() -> int:
    load_dotenv()
    settings = Settings.from_env()

    print("Credentials")
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        present = bool(os.environ.get(name, "").strip())
        print(f"  {name:<20} {'set' if present else 'not set'}")

    if not os.environ.get("OPENAI_API_KEY", "").strip():
        print("\nAdd OPENAI_API_KEY to .env, then run this again.")
        return 1

    print("\nAsking the account which models it can use...")
    try:
        models = available_openai_models()
    except LLMError as exc:
        print(f"  failed: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"  failed: {type(exc).__name__}: {exc}")
        print("  (an authentication error here means the key is wrong or has no access)")
        return 1

    chat_models = [m for m in models if not any(word in m for word in EXCLUDE)]
    print(f"  {len(models)} models visible, {len(chat_models)} usable for chat")

    fast = pick(models, FAST_PREFERENCE)
    reasoning = pick(models, REASONING_PREFERENCE)
    print("\nSuggested routing")
    print(f"  MODEL_FAST={fast or '<none found>'}")
    print(f"  MODEL_REASONING={reasoning or '<none found>'}")
    print("\nAvailable chat-capable models:")
    for model in chat_models[:40]:
        print(f"  {model}")
    if len(chat_models) > 40:
        print(f"  ... and {len(chat_models) - 40} more")

    configured = settings.model_fast or fast
    if not configured:
        print("\nNo usable model found. Set MODEL_FAST in .env manually.")
        return 1

    print(f"\nTest call against {configured} ...")
    os.environ.setdefault("MODEL_FAST", configured)
    client = LLM(Settings.from_env())
    try:
        result = client.complete(
            "Return the number 42 as the field 'answer' and the word 'ok' as 'status'.",
            schema={
                "type": "object",
                "required": ["answer", "status"],
                "properties": {"answer": {"type": "number"}, "status": {"type": "string"}},
            },
            system="You are a test harness. Reply with JSON only.",
            tier="fast",
            max_tokens=100,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  failed: {type(exc).__name__}: {exc}")
        return 1

    print(f"  reply: {result}")
    print(f"  usage: {client.usage.as_dict()}")
    print("\nWorking. Put these two lines in .env:")
    print(f"  MODEL_FAST={fast or configured}")
    print(f"  MODEL_REASONING={reasoning or fast or configured}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
