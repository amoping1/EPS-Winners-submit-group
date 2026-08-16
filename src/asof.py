"""Point-in-time cutoff enforcement.

This module is the centrepiece of the system. One mechanism serves three purposes:

* **Backtesting.** Replaying a past reporting event is only honest if the pipeline
  cannot see anything published on or after that event's report date.
* **Reproducibility.** A judge rerunning the competition command next week gets the
  same forecasts, because nothing published after the cutoff can enter the pipeline.
* **Reuse.** Dropping the cutoff makes the agent usable for future earnings events.

The rule is enforced in one place rather than by discipline at each call site.
Retrieval code asks for the active guard; if no guard is configured the call fails
loudly instead of quietly returning documents from the future.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable, Iterator, Sequence, TypeVar

from .errors import AsOfLeakError, GuardNotConfiguredError

T = TypeVar("T")

# Accepted textual forms for a publication date. The corpus uses ISO dates in its
# frontmatter, but web-cache entries and extracted values are less predictable.
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d %B %Y", "%d %b %Y")


def parse_published_at(value: Any) -> date | None:
    """Coerce a publication date into a ``date``, returning ``None`` if unknown."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # Tolerate full timestamps such as "2026-05-19T12:00:00Z".
        head = text.replace("Z", "").split("T", 1)[0].strip()
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(head, fmt).date()
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


@dataclass
class GuardStats:
    """Counters describing what the guard has seen, surfaced in the dashboard."""

    checked: int = 0
    allowed: int = 0
    blocked: int = 0
    unknown_date: int = 0
    blocked_samples: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "allowed": self.allowed,
            "blocked": self.blocked,
            "unknown_date": self.unknown_date,
            "blocked_samples": list(self.blocked_samples),
        }


class AsOfGuard:
    """Decides whether a document may be used, given a point-in-time cutoff.

    The cutoff is *inclusive*: a document published exactly on ``as_of`` is allowed.
    Backtests therefore construct a guard with ``as_of = report_date - 1 day`` via
    :meth:`strictly_before`, so the report being predicted is itself invisible.
    """

    _MAX_BLOCKED_SAMPLES = 25

    def __init__(
        self,
        as_of: date,
        *,
        label: str = "run",
        allow_unknown_dates: bool = False,
    ) -> None:
        if not isinstance(as_of, date) or isinstance(as_of, datetime):
            raise TypeError("as_of must be a datetime.date")
        self.as_of = as_of
        self.label = label
        self.allow_unknown_dates = allow_unknown_dates
        self.stats = GuardStats()
        self._lock = threading.Lock()

    @classmethod
    def strictly_before(cls, report_date: date, **kwargs: Any) -> "AsOfGuard":
        """Build a guard that excludes anything published on or after ``report_date``."""
        return cls(report_date - timedelta(days=1), **kwargs)

    def is_allowed(self, published_at: Any, *, source: str = "") -> bool:
        """Return whether material with this publication date may be used."""
        parsed = parse_published_at(published_at)
        with self._lock:
            self.stats.checked += 1
            if parsed is None:
                self.stats.unknown_date += 1
                permitted = self.allow_unknown_dates
            else:
                permitted = parsed <= self.as_of
            if permitted:
                self.stats.allowed += 1
            else:
                self.stats.blocked += 1
                if len(self.stats.blocked_samples) < self._MAX_BLOCKED_SAMPLES:
                    self.stats.blocked_samples.append(
                        {
                            "source": source or "<unknown>",
                            "published_at": str(published_at),
                        }
                    )
        return permitted

    def assert_allowed(self, published_at: Any, *, source: str) -> None:
        """Raise :class:`AsOfLeakError` if this material is past the cutoff.

        Used where a document is actually opened, as a second line of defence
        behind :meth:`filter`.
        """
        if not self.is_allowed(published_at, source=source):
            raise AsOfLeakError(
                f"{source!r} is dated {published_at!r}, which is after the "
                f"{self.label} cutoff of {self.as_of.isoformat()}"
            )

    def filter(
        self,
        items: Iterable[T],
        key: Callable[[T], Any],
        *,
        source: Callable[[T], str] | None = None,
    ) -> list[T]:
        """Return only the items published on or before the cutoff."""
        kept: list[T] = []
        for item in items:
            label = source(item) if source else ""
            if self.is_allowed(key(item), source=label):
                kept.append(item)
        return kept

    def describe(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "label": self.label,
            "allow_unknown_dates": self.allow_unknown_dates,
            "stats": self.stats.as_dict(),
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"AsOfGuard(as_of={self.as_of.isoformat()!r}, label={self.label!r})"


# --------------------------------------------------------------------------
# Active guard registry
#
# Thread-local so the four company pipelines can run concurrently, with a global
# fallback set by the orchestrator before fan-out.
# --------------------------------------------------------------------------

_local = threading.local()
_global_guard: AsOfGuard | None = None


def set_guard(guard: AsOfGuard | None) -> None:
    """Install the process-wide guard. Called once by the orchestrator."""
    global _global_guard
    _global_guard = guard


def active_guard() -> AsOfGuard | None:
    """Return the guard in force, or ``None`` if retrieval is not yet configured."""
    return getattr(_local, "guard", None) or _global_guard


def get_guard() -> AsOfGuard:
    """Return the guard in force, raising if none is configured.

    Every retrieval path calls this. An unguarded retrieval is a bug, not a
    default, so it fails rather than returning unfiltered documents.
    """
    guard = active_guard()
    if guard is None:
        raise GuardNotConfiguredError(
            "No point-in-time guard is active. Retrieval must run inside a "
            "configured run; see src.asof.set_guard or src.asof.using."
        )
    return guard


@contextmanager
def using(guard: AsOfGuard) -> Iterator[AsOfGuard]:
    """Temporarily install ``guard`` on the current thread.

    Backtest replays use this so each historical event gets its own cutoff without
    disturbing the competition run's guard.
    """
    previous = getattr(_local, "guard", None)
    _local.guard = guard
    try:
        yield guard
    finally:
        _local.guard = previous


def assert_no_leak(
    documents: Sequence[Any],
    key: Callable[[Any], Any],
    *,
    source: Callable[[Any], str] | None = None,
) -> None:
    """Verify a finished result set contains nothing past the cutoff.

    Run after retrieval as an independent audit: :meth:`AsOfGuard.filter` deciding
    correctly and this check agreeing are two different failure modes.
    """
    guard = get_guard()
    for document in documents:
        published_at = parse_published_at(key(document))
        label = source(document) if source else "<result>"
        if published_at is None:
            if not guard.allow_unknown_dates:
                raise AsOfLeakError(f"{label!r} has no publication date")
            continue
        if published_at > guard.as_of:
            raise AsOfLeakError(
                f"{label!r} is dated {published_at.isoformat()}, past the "
                f"{guard.label} cutoff of {guard.as_of.isoformat()}"
            )
