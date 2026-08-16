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
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import Settings, load_dotenv  # noqa: E402
from src.llm import LLM, LLMError, available_openai_models  # noqa: E402

# Model names are ranked by parsed version rather than matched against a fixed
# list, for the same reason the model id is not hardcoded: this key already
# offers versions newer than any list written today would know about.
VERSION_RE = re.compile(r"^gpt-(\d+)(?:\.(\d+))?")

# Non-chat and specialist endpoints, plus codex variants, which are tuned for
# code rather than for reading filings.
EXCLUDE = (
    "audio", "realtime", "transcribe", "tts", "image", "embedding", "moderation",
    "search", "codex", "instruct", "babbage", "davinci", "sora", "whisper",
)

SMALL_SUFFIXES = ("-mini", "-nano")


def version_of(model: str) -> tuple[int, int]:
    match = VERSION_RE.match(model)
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2) or 0))


def is_small(model: str) -> bool:
    return any(suffix in model for suffix in SMALL_SUFFIXES)


def pick(models: list[str], *, small: bool) -> str | None:
    """Newest usable model, preferring a small variant for the fast tier.

    Dated snapshots are skipped in favour of the rolling family name: the
    snapshot is what the family points at today anyway, and the family keeps
    working when the snapshot is retired.
    """
    usable = [
        model
        for model in models
        if not any(word in model for word in EXCLUDE)
        and "-202" not in model
        and "chat-latest" not in model
        and version_of(model) != (0, 0)
        and is_small(model) == small
    ]
    if not usable:
        return None
    # Newest version first; among equals prefer the plain name over "-pro",
    # which is slower and dearer than this workload needs.
    usable.sort(key=lambda m: (version_of(m), "-pro" not in m), reverse=True)
    return usable[0]


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

    fast = pick(models, small=True)
    reasoning = pick(models, small=False)
    print("\nSuggested routing")
    print(f"  MODEL_FAST={fast or '<none found>'}")
    print(f"  MODEL_REASONING={reasoning or '<none found>'}")
    families = sorted({m.split("-202")[0] for m in chat_models})
    print("\nModel families the key can use:")
    for name in families:
        print(f"  {name}")

    configured = settings.model_fast or fast
    if not configured:
        print("\nNo usable model found. Set MODEL_FAST in .env manually.")
        return 1

    print(f"\nTest call against {configured} ...")
    # Assignment, not setdefault: .env ships with MODEL_FAST= empty, which is
    # already present in the environment and would block a default.
    os.environ["MODEL_FAST"] = configured
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

