"""Append-only run logging with secret redaction.

One JSONL file per run serves three purposes at once: it is the timestamped
clear-run log the competition requires, the data source for the dashboard's agent
activity timeline, and the trace a judge reads to verify the system ran as
described.

Everything written passes through :class:`Redactor` first. Credentials must never
reach a file that gets committed, uploaded or shown on a projector.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# Environment variables whose *values* should never appear in a log line.
_SECRET_ENV_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.IGNORECASE)

# Shapes that look like credentials regardless of where they came from.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)\bapi[_-]?key\s*[=:]\s*[A-Za-z0-9._\-]{16,}"),
)

_REDACTED = "[REDACTED]"
_MAX_STRING = 4000


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string with a trailing Z."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Redactor:
    """Removes credentials from anything on its way to disk or the console."""

    def __init__(self, extra_secrets: tuple[str, ...] = ()) -> None:
        self._literals: list[tuple[str, str]] = []
        for name, value in os.environ.items():
            if _SECRET_ENV_NAME.search(name) and value and len(value) >= 8:
                self._literals.append((value, f"[REDACTED:{name}]"))
        for value in extra_secrets:
            if value and len(value) >= 8:
                self._literals.append((value, _REDACTED))
        # Longest first, so a key that contains another string is masked whole.
        self._literals.sort(key=lambda pair: len(pair[0]), reverse=True)

    def scrub_text(self, text: str, *, truncate: bool = True) -> str:
        """Redact credentials. Truncation applies to log fields, not artifacts.

        A log line with a 200 KB prompt in it is unreadable, so log fields are
        capped. Artifact files are the dashboard's data source and must survive
        intact, so they pass ``truncate=False``.
        """
        for literal, replacement in self._literals:
            if literal in text:
                text = text.replace(literal, replacement)
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub(_REDACTED, text)
        if truncate and len(text) > _MAX_STRING:
            text = text[:_MAX_STRING] + f"...[truncated {len(text) - _MAX_STRING} chars]"
        return text

    def scrub(self, value: Any, *, truncate: bool = True) -> Any:
        """Recursively redact strings inside dicts, lists and tuples."""
        if isinstance(value, str):
            return self.scrub_text(value, truncate=truncate)
        if isinstance(value, dict):
            return {str(k): self.scrub(v, truncate=truncate) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.scrub(item, truncate=truncate) for item in value]
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return self.scrub_text(str(value), truncate=truncate)


class RunLogger:
    """Thread-safe, append-only JSONL logger.

    Safe to share across the four concurrent company pipelines: every write takes
    a lock and carries a monotonically increasing sequence number, so the event
    order in the file is the true order of execution.
    """

    def __init__(
        self,
        run_id: str,
        log_path: Path,
        *,
        redactor: Redactor | None = None,
        echo: bool = True,
    ) -> None:
        self.run_id = run_id
        self.path = Path(log_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.redactor = redactor or Redactor()
        self.echo = echo
        self._lock = threading.Lock()
        self._seq = 0
        self._handle = self.path.open("a", encoding="utf-8")

    # -- writing -----------------------------------------------------------

    def event(self, event_type: str, **fields: Any) -> dict[str, Any]:
        """Write one event. Returns the record actually written, post-redaction."""
        with self._lock:
            self._seq += 1
            record = {
                "ts": utc_now_iso(),
                "run_id": self.run_id,
                "seq": self._seq,
                "type": event_type,
                **self.redactor.scrub(fields),
            }
            self._handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            self._handle.flush()
        if self.echo:
            print(f"[{record['ts']}] {event_type} {self._summarise(fields)}", flush=True)
        return record

    def info(self, message: str, **fields: Any) -> None:
        self.event("info", message=message, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self.event("warning", message=message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self.event("error", message=message, **fields)

    @contextmanager
    def stage(self, name: str, **fields: Any) -> Iterator[dict[str, Any]]:
        """Time a unit of work and record its start, end and any failure."""
        state: dict[str, Any] = {}
        started = time.monotonic()
        self.event("stage.start", stage=name, **fields)
        try:
            yield state
        except Exception as exc:
            self.event(
                "stage.error",
                stage=name,
                duration_s=round(time.monotonic() - started, 3),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        else:
            self.event(
                "stage.end",
                stage=name,
                duration_s=round(time.monotonic() - started, 3),
                **state,
            )

    def close(self) -> None:
        with self._lock:
            if not self._handle.closed:
                self._handle.close()

    def __enter__(self) -> "RunLogger":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _summarise(fields: dict[str, Any], limit: int = 120) -> str:
        if not fields:
            return ""
        parts = []
        for key, value in fields.items():
            text = str(value)
            if len(text) > 60:
                text = text[:57] + "..."
            parts.append(f"{key}={text}")
        summary = " ".join(parts)
        return summary if len(summary) <= limit else summary[: limit - 3] + "..."
