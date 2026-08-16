"""Pre-submission validation of the twelve forecasts.

The organisers' validator checks that a workbook is well-formed. It does not
check that 41.8 was written where 41,800 belonged, or that a margin came out at
270%. Those mistakes cost a metric the maximum penalty just as surely as a
malformed file, and they are invisible until the company reports.

So this module applies the checks a careful analyst would apply before pressing
upload: are the units the right order of magnitude, is the value inside a sane
band for its kind, is it consistent with the company's own recent history, and
do any two of the twelve figures contradict each other.

Every check produces a pass, warn or fail record with a reason, and all of them
are shown in the dashboard -- including the ones that fired. A caught and
corrected mistake is evidence the system works, not something to hide.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .config import Company, Metric
from .corpus import CorpusIndex
from .series import MetricSeries, build_series

PASS = "pass"
WARN = "warn"
FAIL = "fail"

# Plausible bands per metric kind. Deliberately wide: the job is to catch a
# figure that is wrong by an order of magnitude, not to second-guess judgement.
PERCENT_BAND = (-100.0, 100.0)
PER_SHARE_BAND = (-100.0, 100.0)
MONEY_BAND = (0.5, 500_000.0)

# A margin outside this range is almost certainly a different quantity.
MARGIN_BAND = (0.0, 100.0)

# How far a forecast may sit from the company's own recent history before it is
# worth flagging. Wide enough to allow a real inflection, narrow enough to catch
# a unit error.
HISTORY_TOLERANCE = 0.60


@dataclass
class Check:
    """One validation result."""

    company: str
    metric: str
    name: str
    status: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "company": self.company,
            "metric": self.metric,
            "check": self.name,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class ValidationReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> None:
        self.checks.append(check)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if check.status == FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [check for check in self.checks if check.status == WARN]

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": len(self.checks),
            "passed": sum(1 for check in self.checks if check.status == PASS),
            "warnings": len(self.warnings),
            "failures": len(self.failures),
            "status": FAIL if self.failures else (WARN if self.warnings else PASS),
            "checks": [check.as_dict() for check in self.checks],
        }


def _band_for(metric: Metric) -> tuple[float, float]:
    if metric.kind == "percent":
        return MARGIN_BAND if "margin" in metric.label.lower() else PERCENT_BAND
    if metric.kind == "per_share":
        return PER_SHARE_BAND
    return MONEY_BAND


def check_units(company: Company, metric: Metric, value: float) -> Check:
    """Is the figure the right order of magnitude for its declared units?

    This is the check that catches 41.8 written where 41,800 belonged.
    """
    low, high = _band_for(metric)
    if metric.kind == "money":
        inside = low <= abs(value) <= high
    else:
        inside = low <= value <= high

    if not inside:
        return Check(
            company.slug,
            metric.label,
            "units",
            FAIL,
            f"{value:,.4g} is outside the plausible band [{low:,.4g}, {high:,.4g}] "
            f"for {metric.units} -- {metric.scale_note}",
        )
    return Check(
        company.slug, metric.label, "units", PASS, f"{value:,.4g} is plausible for {metric.units}"
    )


def check_against_history(
    company: Company, metric: Metric, value: float, series: MetricSeries
) -> Check:
    """Is the figure near the company's own recent reporting?"""
    latest = series.latest()
    if latest is None:
        return Check(
            company.slug,
            metric.label,
            "history",
            WARN,
            "no historical observations available to compare against",
        )

    scale = max(abs(latest.value), 1e-9)
    divergence = abs(value - latest.value) / scale
    detail = (
        f"{value:,.4g} against most recent reported {latest.value:,.4g} "
        f"on {latest.published_at} ({divergence:+.0%})"
    )
    if divergence > HISTORY_TOLERANCE:
        return Check(company.slug, metric.label, "history", WARN, detail + " -- unusually far")
    return Check(company.slug, metric.label, "history", PASS, detail)


def check_sign(company: Company, metric: Metric, value: float) -> Check:
    """Metrics that cannot sensibly be negative."""
    never_negative = metric.kind == "money" and any(
        word in metric.label.lower() for word in ("sales", "revenue", "fees")
    )
    if never_negative and value <= 0:
        return Check(
            company.slug, metric.label, "sign", FAIL, f"{value:,.4g} must be positive"
        )
    return Check(company.slug, metric.label, "sign", PASS, "sign is sensible")


def check_completeness(company: Company, forecasts: dict[str, float]) -> list[Check]:
    """Every metric must carry a number. A blank scores the maximum penalty."""
    checks: list[Check] = []
    for metric in company.metrics:
        value = forecasts.get(metric.label)
        if value is None:
            checks.append(
                Check(company.slug, metric.label, "completeness", FAIL, "no forecast produced")
            )
        elif value != value or value in (float("inf"), float("-inf")):  # NaN or infinity
            checks.append(
                Check(company.slug, metric.label, "completeness", FAIL, f"value is {value!r}")
            )
        else:
            checks.append(
                Check(company.slug, metric.label, "completeness", PASS, "forecast present")
            )
    return checks


def check_internal_consistency(company: Company, forecasts: dict[str, float]) -> list[Check]:
    """Do two of a company's three figures contradict each other?

    Where a company reports both a revenue-like figure and a profit-like figure,
    the implied margin must be sane. A profit larger than revenue, or a margin of
    90% for a machinery maker, means one of the two is the wrong quantity.
    """
    checks: list[Check] = []
    revenue_label = next(
        (
            metric.label
            for metric in company.metrics
            if metric.kind == "money"
            and any(word in metric.label.lower() for word in ("sales", "revenue", "fees"))
        ),
        None,
    )
    profit_label = next(
        (
            metric.label
            for metric in company.metrics
            if metric.kind == "money" and "profit" in metric.label.lower()
        ),
        None,
    )
    if not revenue_label or not profit_label:
        return checks

    revenue = forecasts.get(revenue_label)
    profit = forecasts.get(profit_label)
    if revenue is None or profit is None or abs(revenue) < 1e-9:
        return checks

    implied_margin = profit / revenue * 100.0
    detail = (
        f"{profit_label} / {revenue_label} implies a {implied_margin:.1f}% margin "
        f"({profit:,.4g} / {revenue:,.4g})"
    )
    if not 0.0 < implied_margin < 60.0:
        checks.append(
            Check(company.slug, "internal consistency", "margin", WARN, detail + " -- implausible")
        )
    else:
        checks.append(Check(company.slug, "internal consistency", "margin", PASS, detail))
    return checks


def validate_company(
    index: CorpusIndex,
    company: Company,
    forecasts: dict[str, float],
    *,
    series_cache: dict[str, MetricSeries] | None = None,
) -> ValidationReport:
    """Run every check for one company's three forecasts."""
    report = ValidationReport()
    for check in check_completeness(company, forecasts):
        report.add(check)

    for metric in company.metrics:
        value = forecasts.get(metric.label)
        if value is None:
            continue
        report.add(check_units(company, metric, value))
        report.add(check_sign(company, metric, value))

        series = (series_cache or {}).get(metric.label)
        if series is None:
            series = build_series(index, company, metric)
        report.add(check_against_history(company, metric, value, series))

    for check in check_internal_consistency(company, forecasts):
        report.add(check)

    return report


def merge(reports: Iterable[ValidationReport]) -> ValidationReport:
    combined = ValidationReport()
    for report in reports:
        combined.checks.extend(report.checks)
    return combined
