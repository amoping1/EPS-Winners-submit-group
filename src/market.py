"""Sell-side consensus as a fourth channel.

The accuracy prize scores our error against Wall Street's on the same metric, so
knowing where the street sits is not optional context -- it is the denominator.
This channel was Adrian's contribution to the team: his system fetches analyst
estimates and reconciles them against the filings.

Live fetching needs yfinance, which is not installed on every machine that has
to run this. So the channel reads a committed snapshot by default and refreshes
it only when the library is present. The snapshot carries its capture timestamp,
which keeps the point-in-time story honest: a figure retrieved on the day is
evidence dated that day, not a value that silently changes on rerun.

Consensus is a rail, never a target. Matching it scores exactly 1.0 against the
benchmark, which is a guaranteed tie and a guaranteed non-win.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import PATHS, Company, Metric

SNAPSHOT = PATHS.root / "data" / "market-consensus.json"

# Metric kinds the street quotes directly. Segment profits and comparable sales
# are not covered by consensus feeds, so those stay unanchored.
REVENUE_WORDS = ("sales", "revenue")


@dataclass
class ConsensusAnchor:
    """What the street expects for one metric."""

    company: str
    metric: str
    units: str
    value: float
    low: float | None = None
    high: float | None = None
    analysts: int | None = None
    captured_at: str = ""
    source: str = ""
    notes: list[str] = field(default_factory=list)

    def gap_from(self, forecast: float) -> float | None:
        """Signed relative distance of a forecast from consensus."""
        if abs(self.value) < 1e-9:
            return None
        return (forecast - self.value) / abs(self.value)

    def as_dict(self) -> dict[str, Any]:
        return {
            "company": self.company,
            "metric": self.metric,
            "units": self.units,
            "value": self.value,
            "low": self.low,
            "high": self.high,
            "analysts": self.analysts,
            "captured_at": self.captured_at,
            "source": self.source,
            "notes": list(self.notes),
        }


def load_snapshot(path: Path | None = None) -> dict[str, Any]:
    target = path or SNAPSHOT
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def anchors_for(company: Company, snapshot: dict[str, Any] | None = None) -> list[ConsensusAnchor]:
    """Consensus anchors for whichever of a company's metrics the street quotes."""
    data = snapshot if snapshot is not None else load_snapshot()
    entry = (data.get("companies") or {}).get(company.slug)
    if not entry:
        return []

    captured = data.get("captured_at", "")
    source = data.get("source", "")
    analysts = entry.get("analysts")
    anchors: list[ConsensusAnchor] = []

    for metric in company.metrics:
        label = metric.label.lower()
        value: float | None = None
        low = high = None

        if metric.kind == "per_share" and entry.get("eps_avg") is not None:
            value = float(entry["eps_avg"])
            low, high = entry.get("eps_low"), entry.get("eps_high")
        elif (
            metric.kind == "money"
            and any(word in label for word in REVENUE_WORDS)
            and entry.get("revenue_avg_m") is not None
        ):
            value = float(entry["revenue_avg_m"])

        if value is None:
            continue

        anchors.append(
            ConsensusAnchor(
                company=company.slug,
                metric=metric.label,
                units=metric.units,
                value=value,
                low=float(low) if low is not None else None,
                high=float(high) if high is not None else None,
                analysts=analysts,
                captured_at=captured,
                source=source,
            )
        )
    return anchors


def anchor_for_metric(
    company: Company, metric: Metric, snapshot: dict[str, Any] | None = None
) -> ConsensusAnchor | None:
    for anchor in anchors_for(company, snapshot):
        if anchor.metric == metric.label:
            return anchor
    return None


def refresh(tickers: dict[str, str], *, timeout: int = 20) -> dict[str, Any] | None:
    """Re-fetch the snapshot, if yfinance is available.

    Returns ``None`` when the library is missing, which is the normal case on a
    machine that only needs to reproduce the committed run.
    """
    try:
        import yfinance  # type: ignore
    except ImportError:
        return None

    from datetime import datetime, timezone

    companies: dict[str, Any] = {}
    for slug, symbol in tickers.items():
        try:
            info = yfinance.Ticker(symbol).info or {}
        except Exception:  # noqa: BLE001 - a missing quote must not stop the run
            continue
        companies[slug] = {
            "symbol": symbol,
            "eps_avg": info.get("targetEpsCurrentQuarter") or info.get("epsCurrentYear"),
            "revenue_avg_m": (
                info.get("revenueEstimateCurrentQuarter") / 1e6
                if info.get("revenueEstimateCurrentQuarter")
                else None
            ),
            "analysts": info.get("numberOfAnalystOpinions"),
        }
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "yfinance",
        "companies": companies,
    }
