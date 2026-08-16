"""Historical series, seasonality, trend and guidance bias.

Everything here is derived from documents the point-in-time guard allowed, so a
series built during a backtest replay contains exactly what was knowable then.

Series are indexed by **publication date**, not by fiscal period. The four
companies have four different fiscal calendars, and the ``period`` field in the
corpus frontmatter is inconsistent -- the same Home Depot earnings call is
labelled both "Q1 2026" and "Q1 2027" across sibling documents. Publication
dates are unambiguous and, for quarterly reporters, order the results correctly
anyway. The period label is carried along as a hint rather than trusted.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Sequence

from .config import Company, Metric
from .corpus import CorpusIndex, Document
from .extract import ValueCandidate, candidates_from_hits, metric_search_terms

# Phrases that mark a figure as forward-looking rather than reported.
GUIDANCE_MARKERS = (
    "we expect",
    "we are forecasting",
    "we anticipate",
    "guidance",
    "outlook",
    "is forecasted",
    "are forecasted",
    "we are guiding",
    "expects to",
    "targeting",
)

# A reporting cycle for a quarterly reporter, with slack for calendar drift.
QUARTER_DAYS = 91
CYCLE_TOLERANCE_DAYS = 45
YEAR_TOLERANCE_DAYS = 60


@dataclass
class SeriesPoint:
    """One observation of a metric, tied to the filing that stated it."""

    published_at: date
    period_label: str
    value: float
    document: Document
    evidence: ValueCandidate
    is_guidance: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "published_at": self.published_at.isoformat(),
            "period_label": self.period_label,
            "value": self.value,
            "is_guidance": self.is_guidance,
            "source": self.document.citation(),
            "quote": self.evidence.context[:280],
        }


@dataclass
class MetricSeries:
    """A metric's observed history, oldest first."""

    company: str
    metric: Metric
    points: list[SeriesPoint] = field(default_factory=list)

    # -- basic access ------------------------------------------------------

    @property
    def reported(self) -> list[SeriesPoint]:
        return [point for point in self.points if not point.is_guidance]

    @property
    def guidance(self) -> list[SeriesPoint]:
        return [point for point in self.points if point.is_guidance]

    def latest(self) -> SeriesPoint | None:
        reported = self.reported
        return reported[-1] if reported else None

    def latest_guidance(self) -> SeriesPoint | None:
        guided = self.guidance
        return guided[-1] if guided else None

    def value_near(self, target: date, tolerance_days: int) -> SeriesPoint | None:
        """The reported observation closest to ``target``, within tolerance."""
        best: SeriesPoint | None = None
        best_gap = tolerance_days + 1
        for point in self.reported:
            gap = abs((point.published_at - target).days)
            if gap <= tolerance_days and gap < best_gap:
                best, best_gap = point, gap
        return best

    def year_ago(self) -> SeriesPoint | None:
        """The comparable observation one year before the latest one."""
        latest = self.latest()
        if latest is None:
            return None
        return self.value_near(latest.published_at - timedelta(days=365), YEAR_TOLERANCE_DAYS)

    # -- derived statistics ------------------------------------------------

    def year_on_year_growth(self) -> float | None:
        """Fractional change against the same period a year earlier."""
        latest, prior = self.latest(), self.year_ago()
        if latest is None or prior is None or abs(prior.value) < 1e-9:
            return None
        return (latest.value - prior.value) / abs(prior.value)

    def trend(self, years: float) -> dict[str, Any] | None:
        """Compound annual growth over a lookback window.

        Reported as a CAGR where the metric is strictly positive, and as an
        average absolute change per year otherwise, because margins and
        comparable-sales percentages can be zero or negative and a ratio would be
        meaningless there.
        """
        reported = self.reported
        if len(reported) < 2:
            return None
        cutoff = reported[-1].published_at - timedelta(days=int(years * 365.25))
        window = [point for point in reported if point.published_at >= cutoff]
        if len(window) < 2:
            return None

        first, last = window[0], window[-1]
        span_years = max((last.published_at - first.published_at).days / 365.25, 1e-6)

        result: dict[str, Any] = {
            "window_years": years,
            "observations": len(window),
            "span_years": round(span_years, 2),
            "from": {"date": first.published_at.isoformat(), "value": first.value},
            "to": {"date": last.published_at.isoformat(), "value": last.value},
            "mean": round(statistics.fmean(p.value for p in window), 4),
        }
        if len(window) > 2:
            result["stdev"] = round(statistics.stdev(p.value for p in window), 4)

        if first.value > 0 and last.value > 0:
            result["cagr"] = round((last.value / first.value) ** (1 / span_years) - 1, 6)
        else:
            result["absolute_change_per_year"] = round(
                (last.value - first.value) / span_years, 6
            )
        return result

    def seasonality(self) -> dict[str, Any] | None:
        """Relative size of each reporting slot within its year.

        Quarterly reporters file in four stable months, so grouping observations
        by calendar month recovers the seasonal shape without needing to model
        four different fiscal calendars. Each observation is expressed as a ratio
        to the average of the surrounding year, and ratios for the same month are
        averaged.
        """
        reported = self.reported
        if len(reported) < 8 or self.metric.kind == "percent":
            # Percentage metrics are already normalised; a seasonal ratio of a
            # margin is not a meaningful quantity.
            return None

        ratios: dict[int, list[float]] = {}
        for index, point in enumerate(reported):
            window = reported[max(0, index - 2) : index + 3]
            baseline = statistics.fmean(item.value for item in window)
            if abs(baseline) < 1e-9:
                continue
            ratios.setdefault(point.published_at.month, []).append(point.value / baseline)

        if len(ratios) < 2:
            return None

        index_by_month = {
            month: round(statistics.fmean(values), 4)
            for month, values in sorted(ratios.items())
            if len(values) >= 2
        }
        if len(index_by_month) < 2:
            return None

        return {
            "by_reporting_month": index_by_month,
            "observations": len(reported),
            "strongest_month": max(index_by_month, key=lambda m: index_by_month[m]),
            "weakest_month": min(index_by_month, key=lambda m: index_by_month[m]),
            "amplitude": round(max(index_by_month.values()) - min(index_by_month.values()), 4),
        }

    def guidance_bias(self) -> dict[str, Any] | None:
        """How this company's guidance has historically compared to its results.

        Each guidance figure is matched to the reported figure that arrived one
        reporting cycle later. Systematic conservatism or optimism is worth real
        accuracy: a company that beats its own guidance by 4% on average makes
        the raw midpoint the wrong anchor.
        """
        pairs: list[dict[str, Any]] = []
        for guided in self.guidance:
            actual = self.value_near(
                guided.published_at + timedelta(days=QUARTER_DAYS), CYCLE_TOLERANCE_DAYS
            )
            if actual is None or abs(guided.value) < 1e-9:
                continue
            pairs.append(
                {
                    "guided_on": guided.published_at.isoformat(),
                    "guided": guided.value,
                    "reported_on": actual.published_at.isoformat(),
                    "reported": actual.value,
                    "surprise": round((actual.value - guided.value) / abs(guided.value), 6),
                }
            )

        if len(pairs) < 3:
            return None

        surprises = [pair["surprise"] for pair in pairs]
        return {
            "observations": len(pairs),
            "median_surprise": round(statistics.median(surprises), 6),
            "mean_surprise": round(statistics.fmean(surprises), 6),
            "stdev_surprise": round(statistics.stdev(surprises), 6) if len(surprises) > 2 else None,
            "beat_rate": round(sum(1 for s in surprises if s > 0) / len(surprises), 4),
            "pairs": pairs[-8:],
        }

    def as_dict(self, max_points: int = 24) -> dict[str, Any]:
        return {
            "company": self.company,
            "metric": self.metric.label,
            "units": self.metric.units,
            "observations": len(self.reported),
            "guidance_observations": len(self.guidance),
            "latest": self.latest().as_dict() if self.latest() else None,
            "year_ago": self.year_ago().as_dict() if self.year_ago() else None,
            "year_on_year_growth": self.year_on_year_growth(),
            "trend": {
                label: self.trend(years)
                for label, years in (("6m", 0.5), ("2y", 2.0), ("5y", 5.0), ("10y", 10.0))
            },
            "seasonality": self.seasonality(),
            "guidance_bias": self.guidance_bias(),
            "points": [point.as_dict() for point in self.reported[-max_points:]],
        }


# --------------------------------------------------------------------------
# Construction
# --------------------------------------------------------------------------


def _looks_like_guidance(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in GUIDANCE_MARKERS)


# Filing kinds, recognised from the corpus filename convention
# (e.g. 2026-05-20__adi-us-20260520-q2-8k__1040581.md).
FILING_KIND_PRIORITY = ("8k", "10q", "10k")


def filing_kind(document: Document) -> str:
    """Classify a filing from its document id."""
    identifier = document.doc_id.lower()
    for kind in FILING_KIND_PRIORITY:
        if f"-{kind}" in identifier:
            return kind
    return "other"


def _series_documents(index: CorpusIndex, company: Company, max_filings: int) -> list[Document]:
    """Choose the filings a series should be read from.

    Earnings releases (8-K) state headline figures in prose with their units:
    "reported sales of $41.8 billion". Quarterly and annual reports state the
    same figures inside large tables surrounded by dozens of other line items,
    where picking the right row is far less reliable -- reading Deere's series
    from 10-Qs produced a latest value of 815 against an actual near 12,000.

    So releases are preferred, and the heavier filings are only drawn on when
    there are too few releases to form a series.
    """
    documents = index.visible_documents(company=company.slug, document_types=["FILING"])
    releases = [document for document in documents if filing_kind(document) == "8k"]
    if len(releases) >= 8:
        return releases[:max_filings]
    return documents[:max_filings]


def build_series(
    index: CorpusIndex,
    company: Company,
    metric: Metric,
    *,
    max_filings: int = 44,
    min_candidate_score: float = 0.0,
) -> MetricSeries:
    """Extract one observation per filing to form a historical series.

    Filings only. Transcripts round and paraphrase figures, and a series mixing
    "$41.8 billion" with "about forty-two billion" produces growth rates that are
    artefacts of the wording rather than the business.
    """
    series = MetricSeries(company=company.slug, metric=metric)
    terms = metric_search_terms(metric)

    for document in _series_documents(index, company, max_filings):
        hits = [
            hit
            for hit in index.document_hits(document.doc_id)
            if any(term.lower() in hit.chunk.text.lower() for term in terms)
        ]
        if not hits:
            continue

        candidates = candidates_from_hits(company, metric, hits, max_candidates=12)
        candidates = [c for c in candidates if c.score > min_candidate_score]
        if not candidates:
            continue

        best = candidates[0]
        series.points.append(
            SeriesPoint(
                published_at=document.published_at or date.min,
                period_label=document.period,
                value=best.value,
                document=document,
                evidence=best,
                is_guidance=_looks_like_guidance(best.context),
            )
        )

    series.points.sort(key=lambda point: point.published_at)
    return series


def build_company_series(
    index: CorpusIndex, company: Company, **kwargs: Any
) -> dict[str, MetricSeries]:
    """Build a series for each of a company's three metrics."""
    return {
        metric.label: build_series(index, company, metric, **kwargs)
        for metric in company.metrics
    }
