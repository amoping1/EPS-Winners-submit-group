"""Point-in-time backtesting.

Replays past reporting events with the pipeline seeing only what was publishable
before the result was announced, then scores the forecast against what the
company actually reported.

The honesty of this rests entirely on the cutoff. For an event announced on
2026-05-19, the replay guard is set to 2026-05-18, so the release being predicted
is invisible along with everything after it. Two independent checks enforce that:
the guard blocks the documents during retrieval, and :func:`assert_no_leak_in`
re-examines the citations of the finished forecast. A leak fails the event rather
than quietly inflating the measured accuracy.

Actual values are extracted from the release itself, with the competition cutoff
in force. Labels have to come from somewhere; what matters is that they never
reach the replay.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Sequence

from . import asof
from .asof import AsOfGuard
from .baseline import estimate_metric
from .config import Company, Metric
from .corpus import CorpusIndex, Document
from .errors import AsOfLeakError
from .extract import candidates_from_hits, wants_annual
from .runlog import RunLogger
from .series import filing_kind

# A metric is only scorable if we can read what the company actually reported.
MIN_EVENTS_FOR_STATS = 2


@dataclass
class BacktestEvent:
    """One past reporting date, with the figures the company announced."""

    company_slug: str
    report_date: date
    document: Document
    actuals: dict[str, float] = field(default_factory=dict)

    @property
    def cutoff(self) -> date:
        """Last date visible to a replay: the day before the announcement."""
        return self.report_date - timedelta(days=1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "company": self.company_slug,
            "report_date": self.report_date.isoformat(),
            "cutoff": self.cutoff.isoformat(),
            "document": self.document.citation(),
            "actuals": dict(self.actuals),
        }


@dataclass
class MetricOutcome:
    """The result of forecasting one metric at one past event."""

    company_slug: str
    metric: str
    units: str
    report_date: date
    forecast: float
    actual: float
    confidence: str
    method: str
    citations: list[str] = field(default_factory=list)

    @property
    def absolute_error(self) -> float:
        return abs(self.forecast - self.actual)

    @property
    def percentage_error(self) -> float | None:
        """Error relative to the actual. Undefined when the actual is ~zero."""
        if abs(self.actual) < 1e-9:
            return None
        return self.absolute_error / abs(self.actual)

    @property
    def directionally_correct(self) -> bool | None:
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "company": self.company_slug,
            "metric": self.metric,
            "units": self.units,
            "report_date": self.report_date.isoformat(),
            "forecast": self.forecast,
            "actual": self.actual,
            "absolute_error": round(self.absolute_error, 6),
            "percentage_error": (
                round(self.percentage_error, 6) if self.percentage_error is not None else None
            ),
            "confidence": self.confidence,
            "method": self.method,
            "citations": list(self.citations),
        }


def assert_no_leak_in(citations: Sequence[Document], cutoff: date, *, context: str) -> None:
    """Second, independent check that a replay saw nothing past its cutoff."""
    for document in citations:
        published = document.published_at
        if published is None or published > cutoff:
            raise AsOfLeakError(
                f"{context}: cited {document.rel_path} dated {published}, "
                f"past the replay cutoff of {cutoff.isoformat()}"
            )


def find_events(
    index: CorpusIndex,
    company: Company,
    *,
    count: int = 8,
    earliest: date | None = None,
) -> list[BacktestEvent]:
    """Recent earnings releases, newest first, one per announcement date.

    Only releases are used. A replay is meant to reproduce the moment a result
    became public, and that moment is the release.
    """
    releases = [
        document
        for document in index.visible_documents(company=company.slug, document_types=["FILING"])
        if filing_kind(document) == "8k" and document.published_at is not None
    ]

    # A replay is only valid if the event reports the same kind of period the
    # forecast targets. Hays' target is a full year, so replaying it against
    # quarterly trading updates compares an annual forecast with a quarter's
    # figures -- which is what drove its measured error to 2,429%.
    if wants_annual(company):
        annual_releases = [
            document
            for document in releases
            if any(marker in document.doc_id.lower() for marker in ("-h2", "-fy", "-annual"))
        ]
        releases = annual_releases if len(annual_releases) >= 3 else releases

    events: list[BacktestEvent] = []
    seen_dates: set[date] = set()
    for document in releases:
        report_date = document.published_at
        if report_date in seen_dates:
            continue
        if earliest and report_date < earliest:
            continue
        seen_dates.add(report_date)
        events.append(
            BacktestEvent(
                company_slug=company.slug, report_date=report_date, document=document
            )
        )
        if len(events) >= count:
            break
    return events


def read_actuals(
    index: CorpusIndex,
    company: Company,
    event: BacktestEvent,
    *,
    same_day_documents: bool = True,
) -> dict[str, float]:
    """Extract what the company reported, from the release itself.

    Companies often file two releases on the same day (the announcement and its
    exhibit), so all documents published that day are read together.
    """
    documents = [event.document]
    if same_day_documents:
        documents = [
            document
            for document in index.visible_documents(
                company=company.slug, document_types=["FILING"]
            )
            if document.published_at == event.report_date
        ] or [event.document]

    hits: list[Any] = []
    for document in documents:
        hits.extend(index.document_hits(document.doc_id))

    actuals: dict[str, float] = {}
    for metric in company.metrics:
        candidates = candidates_from_hits(company, metric, hits, max_candidates=8)
        if candidates:
            actuals[metric.label] = candidates[0].value
    return actuals


def replay_event(
    index: CorpusIndex,
    company: Company,
    event: BacktestEvent,
    *,
    logger: RunLogger | None = None,
) -> list[MetricOutcome]:
    """Forecast one past event using only what was knowable before it."""
    guard = AsOfGuard(event.cutoff, label=f"backtest {company.slug} {event.report_date}")
    outcomes: list[MetricOutcome] = []

    with asof.using(guard):
        for metric in company.metrics:
            actual = event.actuals.get(metric.label)
            if actual is None:
                continue

            estimate = estimate_metric(index, company, metric)
            cited = [candidate.document for candidate in estimate.candidates]
            assert_no_leak_in(
                cited, event.cutoff, context=f"{company.slug} {metric.label} {event.report_date}"
            )

            outcomes.append(
                MetricOutcome(
                    company_slug=company.slug,
                    metric=metric.label,
                    units=metric.units,
                    report_date=event.report_date,
                    forecast=estimate.value,
                    actual=actual,
                    confidence=estimate.confidence,
                    method=estimate.method,
                    citations=[document.rel_path for document in cited[:3]],
                )
            )

    if logger:
        logger.event(
            "backtest.event",
            company=company.slug,
            report_date=event.report_date.isoformat(),
            cutoff=event.cutoff.isoformat(),
            metrics=len(outcomes),
            documents_blocked=guard.stats.blocked,
        )
    return outcomes


def summarise(outcomes: Sequence[MetricOutcome]) -> dict[str, Any]:
    """Error statistics per metric, which is what calibration needs."""
    by_metric: dict[tuple[str, str], list[MetricOutcome]] = {}
    for outcome in outcomes:
        by_metric.setdefault((outcome.company_slug, outcome.metric), []).append(outcome)

    metrics: list[dict[str, Any]] = []
    for (company_slug, metric_label), group in sorted(by_metric.items()):
        percentage_errors = [
            outcome.percentage_error
            for outcome in group
            if outcome.percentage_error is not None
        ]
        entry: dict[str, Any] = {
            "company": company_slug,
            "metric": metric_label,
            "units": group[0].units,
            "events": len(group),
            "median_absolute_error": round(
                statistics.median(outcome.absolute_error for outcome in group), 6
            ),
        }
        if percentage_errors:
            entry["median_percentage_error"] = round(statistics.median(percentage_errors), 6)
            entry["mean_percentage_error"] = round(statistics.fmean(percentage_errors), 6)
            entry["best_percentage_error"] = round(min(percentage_errors), 6)
            entry["worst_percentage_error"] = round(max(percentage_errors), 6)
            if len(percentage_errors) >= MIN_EVENTS_FOR_STATS:
                entry["within_5pct"] = round(
                    sum(1 for error in percentage_errors if error <= 0.05)
                    / len(percentage_errors),
                    4,
                )
        metrics.append(entry)

    scored = [
        entry["median_percentage_error"]
        for entry in metrics
        if "median_percentage_error" in entry
    ]
    return {
        "metrics": metrics,
        "overall": {
            "scored_metrics": len(scored),
            "median_percentage_error": (
                round(statistics.median(scored), 6) if scored else None
            ),
            "outcomes": len(outcomes),
        },
    }


def run_backtest(
    index: CorpusIndex,
    companies: Sequence[Company],
    *,
    events_per_company: int = 8,
    logger: RunLogger | None = None,
) -> dict[str, Any]:
    """Replay recent reporting events for every company and score the results."""
    all_outcomes: list[MetricOutcome] = []
    all_events: list[BacktestEvent] = []
    leaks: list[str] = []

    for company in companies:
        events = find_events(index, company, count=events_per_company)
        for event in events:
            event.actuals = read_actuals(index, company, event)
            if not event.actuals:
                continue
            all_events.append(event)
            try:
                all_outcomes.extend(replay_event(index, company, event, logger=logger))
            except AsOfLeakError as exc:
                # A leak invalidates the event; it must never be scored anyway.
                leaks.append(str(exc))
                if logger:
                    logger.error(f"backtest leak: {exc}", company=company.slug)

    report = summarise(all_outcomes)
    report["events"] = [event.as_dict() for event in all_events]
    report["outcomes"] = [outcome.as_dict() for outcome in all_outcomes]
    report["leakage"] = {
        "detected": len(leaks),
        "events_replayed": len(all_events),
        "status": "clean" if not leaks else "FAILED",
        "details": leaks[:10],
    }
    return report
