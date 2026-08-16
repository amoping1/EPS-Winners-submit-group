"""Deterministic baseline estimates.

This is the floor of the system, not its ceiling. It needs no API key and no
model call: it searches the corpus for each metric, extracts plausible values
with their provenance, and takes a robust central value.

Two reasons it exists in its own module:

* **The safety line.** From the moment this works, four valid workbooks can
  always be produced. Every later stage improves numbers that already exist
  rather than being the only thing standing between us and an empty submission.
* **The degradation path.** When the spend ceiling is reached or a model call
  fails, the pipeline falls back here rather than leaving a cell empty, because a
  missing forecast scores the maximum penalty while a weak one rarely does.

It is deliberately naive: it reads recent reported figures rather than reasoning
about what the next period will bring. The agent layer replaces its output; this
module only guarantees the output is never missing.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from datetime import timedelta

from .config import Company, Metric
from .corpus import CorpusIndex
from .extract import ValueCandidate, candidates_from_hits, metric_query
from .series import QUARTER_DAYS, YEAR_TOLERANCE_DAYS, build_series

# How many top-scoring candidates feed the median. Small enough to stay close to
# the best evidence, large enough that one mis-parsed figure cannot decide it.
CONSENSUS_WINDOW = 7


@dataclass
class BaselineEstimate:
    """A metric value derived without any model call."""

    company: str
    metric: str
    units: str
    value: float
    confidence: str
    method: str
    candidates: list[ValueCandidate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self, max_candidates: int = 6) -> dict[str, Any]:
        return {
            "company": self.company,
            "metric": self.metric,
            "units": self.units,
            "value": self.value,
            "confidence": self.confidence,
            "method": self.method,
            "notes": list(self.notes),
            "evidence": [c.as_dict() for c in self.candidates[:max_candidates]],
        }


def _magnitude(value: float) -> int:
    """Order of magnitude of a value, used to group like with like."""
    magnitude = abs(value)
    if magnitude < 1e-9:
        return 0
    return int(math.floor(math.log10(magnitude)))


def _consensus_around_best(
    candidates: list[ValueCandidate],
    *,
    tolerance: float = 0.25,
) -> list[ValueCandidate]:
    """Corroborate the best-evidenced candidate; never outvote it.

    Aggregating across all candidates does not work, because the text around a
    metric is full of quantities that are not the metric: note references, page
    numbers, share counts, prior-year comparatives and neighbouring line items.

    Two aggregations were tried and measured against known figures:

    * A plain median put Home Depot's net sales at 220 USDm against an actual
      near 45,000, because it averaged across unrelated quantities.
    * Grouping by order of magnitude and letting scores vote was worse still: a
      dozen incidental single-digit matches outvoted the single passage stating
      "$41.8 billion", and the estimate collapsed to 6.

    The ranking already encodes what matters -- passage relevance, proximity to
    the metric name, and whether the figure carries an explicit unit. So the
    top-scoring candidate sets the answer, and only candidates within
    ``tolerance`` of it are allowed to refine it. Corroboration sharpens the
    number; it can no longer replace it with a different quantity.
    """
    best = candidates[0]
    scale = max(abs(best.value), 1e-9)
    return [
        candidate
        for candidate in candidates
        if abs(candidate.value - best.value) / scale <= tolerance
    ]


def _seasonal_naive_estimate(
    index: CorpusIndex, company: Company, metric: Metric
) -> BaselineEstimate | None:
    """Seasonal naive forecast with drift, from the historical series.

    The next period is most like the same period a year ago, moved by however
    much the business has grown since. For a seasonal retailer that is far better
    than carrying the last quarter forward: Home Depot's Q2 is structurally
    larger than its Q1, and a persistence forecast misses that every year.

    Falls back down a chain -- prior-year period grown by recent growth, then the
    latest value grown, then the latest value unchanged -- so a thin series still
    produces a number.
    """
    series = build_series(index, company, metric)
    latest = series.latest()
    if latest is None:
        return None

    growth = series.year_on_year_growth()
    # Percentage metrics are rates, not levels: growing a margin by a growth rate
    # compounds a quantity that does not compound.
    if metric.kind == "percent":
        growth = None

    next_report = latest.published_at + timedelta(days=QUARTER_DAYS)
    prior_year = series.value_near(next_report - timedelta(days=365), YEAR_TOLERANCE_DAYS)

    if prior_year is not None and growth is not None:
        value = prior_year.value * (1.0 + growth)
        method = (
            f"seasonal naive with drift: {prior_year.published_at} value "
            f"{prior_year.value:,.4g} grown by {growth:+.1%} year-on-year"
        )
        anchor = prior_year
    elif prior_year is not None:
        value = prior_year.value
        method = f"seasonal naive: same period a year earlier ({prior_year.published_at})"
        anchor = prior_year
    elif growth is not None:
        value = latest.value * (1.0 + growth)
        method = f"persistence with drift: latest value grown by {growth:+.1%}"
        anchor = latest
    else:
        value = latest.value
        method = "persistence: most recent reported value"
        anchor = latest

    observations = len(series.reported)
    confidence = "medium" if observations >= 8 and prior_year is not None else "low"

    return BaselineEstimate(
        company=company.slug,
        metric=metric.label,
        units=metric.units,
        value=float(value),
        confidence=confidence,
        method=method,
        candidates=[anchor.evidence, latest.evidence] if anchor is not latest else [latest.evidence],
        notes=[
            f"series has {observations} reported observations from earnings releases",
            f"latest reported {latest.value:,.4g} on {latest.published_at}",
            f"units: {metric.scale_note}",
        ],
    )


def _fallback_value(metric: Metric) -> tuple[float, str]:
    """Last resort when the corpus yields nothing usable for a metric.

    Returning a number is mandatory. A percentage metric defaults to no change,
    which is a defensible neutral forecast; the other kinds have no neutral
    value, so they are flagged loudly for the validation layer to escalate.
    """
    if metric.kind == "percent":
        return 0.0, "no candidates found; assuming no change year on year"
    return 0.0, "no candidates found; placeholder requires escalation"


def estimate_metric(
    index: CorpusIndex,
    company: Company,
    metric: Metric,
    *,
    consensus_window: int = CONSENSUS_WINDOW,
) -> BaselineEstimate:
    """Estimate one metric, preferring the historical series over a raw search.

    The series is built by reading earnings releases end to end, which is far
    more reliable than searching for passages and hoping the top hit is the
    headline figure. Measured on backtested Home Depot events, the search route
    produced 2,600 / 18 / 202 USDm against actuals of 41,800 / 2,500 / 41,400;
    the series route reproduces the reported figures.
    """
    seasonal = _seasonal_naive_estimate(index, company, metric)
    if seasonal is not None:
        return seasonal

    query = metric_query(company, metric)

    # Filings first: they state figures precisely. Transcripts round and
    # paraphrase, so they are a fallback rather than a primary source.
    hits = index.search(query, company=company.slug, document_types=["FILING"], limit=14)
    source = "filings"
    if len(hits) < 4:
        hits = index.search(query, company=company.slug, limit=16)
        source = "filings and transcripts"

    candidates = candidates_from_hits(company, metric, hits)
    if not candidates:
        value, note = _fallback_value(metric)
        return BaselineEstimate(
            company=company.slug,
            metric=metric.label,
            units=metric.units,
            value=value,
            confidence="fallback",
            method="fallback",
            notes=[note],
        )

    agreeing = _consensus_around_best(candidates)
    window = agreeing[:consensus_window]
    value = float(statistics.median(candidate.value for candidate in window))
    best = candidates[0]

    # One passage stating a figure is weaker evidence than several agreeing on
    # it, and that is exactly what confidence should express.
    confidence = "medium" if len(window) >= 3 else "low"

    return BaselineEstimate(
        company=company.slug,
        metric=metric.label,
        units=metric.units,
        value=value,
        confidence=confidence,
        method=(
            f"best-evidenced candidate refined by {len(window)} corroborating "
            f"value(s) within 25%, from {source}"
        ),
        candidates=candidates,
        notes=[
            f"top candidate {best.raw!r} = {best.value:.4g} {metric.units}",
            f"{len(agreeing)} of {len(candidates)} candidates agreed within 25%",
            f"units: {metric.scale_note}",
        ],
    )


def estimate_company(index: CorpusIndex, company: Company) -> dict[str, BaselineEstimate]:
    """Estimate all three metrics for one company."""
    return {
        metric.label: estimate_metric(index, company, metric) for metric in company.metrics
    }
