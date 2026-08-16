"""Consensus across the three independently built systems.

Three people built three forecasting systems from the same brief, the same
frozen corpus and the same twelve targets, without sharing code. That makes
their outputs genuinely independent estimates of the same quantities, which is
the one situation where an ensemble reliably beats its members: independent
errors partly cancel, correlated ones do not.

The rule is the classic two-against-one. Sort the three estimates; if the gap on
one side is more than twice the gap on the other, the far value is an outlier and
the two that agree are averaged. Otherwise the median stands. That is
deliberately simple, because with three points anything more elaborate is fitting
noise.

**Why the ensemble is not just set to the analyst consensus.** The accuracy prize
divides our error by Wall Street's on the same metric. Matching consensus scores
1.0 by construction -- a guaranteed tie and a guaranteed non-win. Consensus is
therefore used as a sanity rail, not a target: it flags where all three of us
have drifted far from the market, but it never replaces our number.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Sequence

# One estimate is treated as an outlier when its gap to the middle value is more
# than this multiple of the other gap.
OUTLIER_RATIO = 2.0

# Agreement is measured relative to the size of the number, so a 2% spread on
# revenue and a 2% spread on a margin are judged the same way.
TIGHT_AGREEMENT = 0.05
LOOSE_AGREEMENT = 0.20

# How far the ensemble may sit from published analyst consensus before it is
# worth flagging for a human. Not a correction -- matching consensus guarantees
# a tie on that metric, so drifting from it is the point.
CONSENSUS_ALERT = 0.15


@dataclass
class Consensus:
    """The ensemble's answer for one metric, and how it was reached."""

    company: str
    metric: str
    units: str
    value: float
    rule: str
    spread: float
    members: dict[str, float] = field(default_factory=dict)
    dropped: str | None = None
    agreement: str = "unknown"
    market_consensus: float | None = None
    market_gap: float | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "company": self.company,
            "metric": self.metric,
            "units": self.units,
            "value": self.value,
            "rule": self.rule,
            "spread": round(self.spread, 4),
            "agreement": self.agreement,
            "members": {k: round(v, 4) for k, v in self.members.items()},
            "dropped": self.dropped,
            "market_consensus": self.market_consensus,
            "market_gap": round(self.market_gap, 4) if self.market_gap is not None else None,
            "notes": list(self.notes),
        }


def _agreement_band(values: Sequence[float]) -> tuple[float, str]:
    """Relative spread of the estimates, and a label for it."""
    scale = max(abs(statistics.median(values)), 1e-9)
    spread = (max(values) - min(values)) / scale
    if spread <= TIGHT_AGREEMENT:
        return spread, "tight"
    if spread <= LOOSE_AGREEMENT:
        return spread, "moderate"
    return spread, "wide"


def combine(
    company: str,
    metric: str,
    units: str,
    estimates: dict[str, float],
    *,
    market: float | None = None,
) -> Consensus:
    """Reduce several independent estimates to one figure."""
    names = list(estimates)
    values = [estimates[name] for name in names]

    if len(values) == 1:
        return Consensus(
            company=company,
            metric=metric,
            units=units,
            value=values[0],
            rule="single estimate",
            spread=0.0,
            members=dict(estimates),
            agreement="single",
        )

    if len(values) == 2:
        spread, band = _agreement_band(values)
        return Consensus(
            company=company,
            metric=metric,
            units=units,
            value=statistics.fmean(values),
            rule="mean of two estimates",
            spread=spread,
            members=dict(estimates),
            agreement=band,
        )

    ordered = sorted(zip(values, names))
    (low, low_name), (mid, mid_name), (high, high_name) = ordered
    lower_gap = mid - low
    upper_gap = high - mid
    spread, band = _agreement_band(values)

    dropped: str | None = None
    if upper_gap > OUTLIER_RATIO * max(lower_gap, 1e-12):
        value = statistics.fmean([low, mid])
        rule = "two agreed, highest discarded as an outlier"
        dropped = high_name
    elif lower_gap > OUTLIER_RATIO * max(upper_gap, 1e-12):
        value = statistics.fmean([mid, high])
        rule = "two agreed, lowest discarded as an outlier"
        dropped = low_name
    else:
        value = mid
        rule = "median of three; no estimate stood apart"

    result = Consensus(
        company=company,
        metric=metric,
        units=units,
        value=float(value),
        rule=rule,
        spread=spread,
        members=dict(estimates),
        dropped=dropped,
        agreement=band,
    )

    if dropped:
        result.notes.append(
            f"{dropped} was {estimates[dropped]:,.4g} against "
            f"{value:,.4g} from the other two"
        )
    if band == "wide":
        result.notes.append(
            "the three systems disagreed by more than 20%, so this figure is "
            "weakly supported however it was combined"
        )

    if market is not None and abs(market) > 1e-9:
        result.market_consensus = market
        result.market_gap = (value - market) / abs(market)
        if abs(result.market_gap) > CONSENSUS_ALERT:
            result.notes.append(
                f"sits {result.market_gap:+.0%} from analyst consensus of "
                f"{market:,.4g}; deviation is intended but this is a large one"
            )
    return result


def build(collected: dict[str, Any]) -> list[Consensus]:
    """Combine every target in a collected team-forecast file."""
    results: list[Consensus] = []
    for entry in collected.get("metrics", []):
        estimates = {
            name: float(item["value"])
            for name, item in entry.get("estimates", {}).items()
            if item.get("value") is not None
        }
        if not estimates:
            continue
        market = (entry.get("market_consensus") or {}).get("value")
        results.append(
            combine(
                entry["company"],
                entry["metric"],
                entry["units"],
                estimates,
                market=float(market) if market is not None else None,
            )
        )
    return results


def summarise(results: Sequence[Consensus]) -> dict[str, Any]:
    bands: dict[str, int] = {}
    dropped: dict[str, int] = {}
    for item in results:
        bands[item.agreement] = bands.get(item.agreement, 0) + 1
        if item.dropped:
            dropped[item.dropped] = dropped.get(item.dropped, 0) + 1
    return {
        "targets": len(results),
        "agreement": bands,
        "outliers_discarded_by_source": dropped,
        "median_spread": round(
            statistics.median([item.spread for item in results]) if results else 0.0, 4
        ),
    }
