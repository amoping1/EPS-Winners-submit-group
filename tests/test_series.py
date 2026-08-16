"""Tests for historical series construction and derived statistics.

The exact-match assertions below are the useful ones: Home Depot's Q1 FY2026
release states a 4.8% sales increase and EPS of $3.30 against $3.45, and Deere's
Q2 release states $6.55 against $6.64. If the series reproduces those, the
extraction chain is reading real reported figures rather than nearby numbers.
"""

from __future__ import annotations

import unittest
from datetime import date

from src import asof
from src.asof import AsOfGuard
from src.config import Metric, find_company
from src.corpus import get_index
from src.series import (
    MetricSeries,
    SeriesPoint,
    build_series,
    filing_kind,
)

COMPETITION_CUTOFF = date(2026, 8, 16)


def setUpModule() -> None:
    global INDEX
    asof.set_guard(AsOfGuard(COMPETITION_CUTOFF))
    INDEX = get_index()
    asof.set_guard(None)


class FilingKindTests(unittest.TestCase):
    def setUp(self):
        asof.set_guard(AsOfGuard(COMPETITION_CUTOFF))

    def tearDown(self):
        asof.set_guard(None)

    def test_earnings_releases_are_recognised(self):
        document = INDEX.documents["2026-05-19__hd-us-20260519-q1-8k__1038584"]
        self.assertEqual(filing_kind(document), "8k")

    def test_quarterly_reports_are_recognised(self):
        document = INDEX.documents["2026-05-19__hd-us-20260519-q1-10q__1053121"]
        self.assertEqual(filing_kind(document), "10q")

    def test_every_company_has_enough_releases_to_build_a_series(self):
        for slug in ("HD", "ADI", "HAS", "DE"):
            company = find_company(slug)
            releases = [
                document
                for document in INDEX.visible_documents(
                    company=company.slug, document_types=["FILING"]
                )
                if filing_kind(document) == "8k"
            ]
            self.assertGreaterEqual(len(releases), 8, msg=slug)


class SeriesConstructionTests(unittest.TestCase):
    def setUp(self):
        asof.set_guard(AsOfGuard(COMPETITION_CUTOFF))

    def tearDown(self):
        asof.set_guard(None)

    def test_points_are_ordered_oldest_first(self):
        company = find_company("HD")
        series = build_series(INDEX, company, company.metrics[0])
        dates = [point.published_at for point in series.points]
        self.assertEqual(dates, sorted(dates))

    def test_every_challenge_metric_yields_a_series(self):
        for slug in ("HD", "ADI", "HAS", "DE"):
            company = find_company(slug)
            for metric in company.metrics:
                series = build_series(INDEX, company, metric)
                self.assertGreaterEqual(
                    len(series.reported), 10, msg=f"{slug}: {metric.label}"
                )

    def test_home_depot_net_sales_matches_the_reported_figure(self):
        company = find_company("HD")
        series = build_series(INDEX, company, company.metric_by_label("Net sales"))
        latest = series.latest()
        self.assertEqual(latest.published_at, date(2026, 5, 19))
        self.assertAlmostEqual(latest.value, 41800.0, delta=100.0)

    def test_home_depot_growth_matches_the_stated_increase(self):
        # "an increase of $1.9 billion, or 4.8% from the first quarter of fiscal 2025"
        company = find_company("HD")
        series = build_series(INDEX, company, company.metric_by_label("Net sales"))
        self.assertAlmostEqual(series.year_on_year_growth(), 0.048, delta=0.01)

    def test_deere_eps_matches_the_reported_figure(self):
        # "$6.55 per share, compared with ... $6.64"
        company = find_company("DE")
        series = build_series(INDEX, company, company.metric_by_label("Diluted EPS (GAAP)"))
        latest = series.latest()
        self.assertAlmostEqual(latest.value, 6.55, delta=0.01)
        self.assertAlmostEqual(series.year_on_year_growth(), -0.0135, delta=0.01)

    def test_series_respects_the_cutoff(self):
        asof.set_guard(AsOfGuard(date(2025, 12, 31)))
        company = find_company("HD")
        series = build_series(INDEX, company, company.metrics[0])
        self.assertTrue(series.reported)
        for point in series.points:
            self.assertLessEqual(point.published_at, date(2025, 12, 31))

    def test_guidance_is_separated_from_reported_values(self):
        company = find_company("ADI")
        series = build_series(INDEX, company, company.metric_by_label("Revenue"))
        self.assertEqual(
            len(series.points), len(series.reported) + len(series.guidance)
        )


class DerivedStatisticsTests(unittest.TestCase):
    """Statistics are checked on a synthetic series with a known shape."""

    def setUp(self):
        asof.set_guard(AsOfGuard(COMPETITION_CUTOFF))
        company = find_company("HD")
        self.index = INDEX
        self.company = company
        self.series = build_series(INDEX, company, company.metric_by_label("Net sales"))

    def tearDown(self):
        asof.set_guard(None)

    def test_trend_windows_are_all_reported(self):
        trends = {label: self.series.trend(years) for label, years in
                  (("6m", 0.5), ("2y", 2.0), ("5y", 5.0), ("10y", 10.0))}
        self.assertIsNotNone(trends["2y"])
        self.assertIsNotNone(trends["5y"])
        self.assertIn("cagr", trends["5y"])

    def test_longer_windows_cover_more_observations(self):
        short = self.series.trend(2.0)
        long = self.series.trend(10.0)
        self.assertGreater(long["observations"], short["observations"])

    def test_seasonality_is_reported_for_a_money_metric(self):
        seasonality = self.series.seasonality()
        self.assertIsNotNone(seasonality)
        self.assertIn("by_reporting_month", seasonality)
        self.assertGreater(seasonality["amplitude"], 0.0)

    def test_seasonality_is_not_computed_for_percentage_metrics(self):
        company = find_company("HD")
        series = build_series(
            self.index, company, company.metric_by_label("Comparable sales, total company")
        )
        self.assertIsNone(series.seasonality())

    def test_year_ago_lookup_tolerates_calendar_drift(self):
        metric = Metric("Test", "USDm")
        series = MetricSeries(company="TEST", metric=metric)
        base = date(2026, 5, 19)
        for offset, value in ((0, 100.0), (358, 90.0), (720, 80.0)):
            series.points.append(
                SeriesPoint(
                    published_at=base - __import__("datetime").timedelta(days=offset),
                    period_label="",
                    value=value,
                    document=next(iter(INDEX.documents.values())),
                    evidence=None,  # type: ignore[arg-type]
                )
            )
        series.points.sort(key=lambda point: point.published_at)
        self.assertEqual(series.year_ago().value, 90.0)

    def test_growth_is_none_without_a_comparable_period(self):
        metric = Metric("Test", "USDm")
        series = MetricSeries(company="TEST", metric=metric)
        self.assertIsNone(series.year_on_year_growth())


if __name__ == "__main__":
    unittest.main()
