"""Tests for the challenge specification loader.

The organisers' validator compares metric labels and units by exact string match,
so these tests assert we read them from ``challenge/companies.json`` rather than
carrying a copy that could drift.
"""

from __future__ import annotations

import unittest

from src.config import (
    PATHS,
    Metric,
    find_company,
    load_companies,
    resolve_corpus_dir,
)
from src.errors import ConfigurationError


class CompanySpecTests(unittest.TestCase):
    def setUp(self):
        self.companies = load_companies()

    def test_four_companies_with_three_metrics_each(self):
        self.assertEqual(len(self.companies), 4)
        for company in self.companies:
            self.assertEqual(len(company.metrics), 3, msg=company.slug)

    def test_expected_tickers_are_present(self):
        self.assertEqual(
            {company.slug for company in self.companies},
            {"HD", "ADI", "HAS", "DE"},
        )

    def test_hays_ticker_shortens_to_has(self):
        hays = find_company("LSE:HAS", self.companies)
        self.assertEqual(hays.slug, "HAS")
        self.assertEqual(hays.period, "FY2026")

    def test_lookup_accepts_ticker_slug_and_name(self):
        for selector in ("HD", "hd", "Home Depot"):
            self.assertEqual(find_company(selector, self.companies).slug, "HD")

    def test_unknown_company_raises(self):
        with self.assertRaises(ConfigurationError):
            find_company("MSFT", self.companies)

    def test_output_filenames_match_the_required_names(self):
        self.assertEqual(
            sorted(company.output_file for company in self.companies),
            [
                "ADI-FY2026Q3.xlsx",
                "DE-FY2026Q3.xlsx",
                "HAS-FY2026.xlsx",
                "HD-FY2026Q2.xlsx",
            ],
        )

    def test_templates_exist_for_every_company(self):
        for company in self.companies:
            template = PATHS.templates / company.output_file
            self.assertTrue(template.exists(), msg=f"missing template for {company.slug}")


class MetricUnitTests(unittest.TestCase):
    def test_percent_metrics_are_classified(self):
        self.assertEqual(Metric("Adjusted gross margin", "%").kind, "percent")

    def test_per_share_metrics_are_classified(self):
        self.assertEqual(Metric("Adjusted diluted EPS", "USD / share").kind, "per_share")
        self.assertEqual(Metric("Pre-exceptional basic EPS", "GBp").kind, "per_share")

    def test_money_metrics_are_classified(self):
        self.assertEqual(Metric("Net sales", "USDm").kind, "money")
        self.assertEqual(Metric("Net fees", "GBPm").kind, "money")

    def test_hays_eps_is_pence_not_pounds(self):
        metric = Metric("Pre-exceptional basic EPS", "GBp")
        self.assertEqual(metric.currency, "GBp")
        self.assertIn("pence", metric.scale_note)

    def test_metric_key_is_filename_safe(self):
        metric = Metric("Comparable sales, total company", "%")
        self.assertEqual(metric.key, "comparable_sales_total_company")

    def test_every_challenge_unit_is_recognised(self):
        for company in load_companies():
            for metric in company.metrics:
                self.assertNotEqual(
                    metric.kind, "unknown", msg=f"{company.slug}: {metric.units}"
                )


class CorpusDirTests(unittest.TestCase):
    """Folder resolution is derived from company metadata, not a hardcoded map."""

    def test_resolves_all_four_company_folders(self):
        expected = {
            "Home Depot": "home-depot",
            "Analog Devices": "analog-devices",
            "Hays plc": "hays",
            "Deere & Company": "deere",
        }
        for name, folder in expected.items():
            self.assertEqual(resolve_corpus_dir(name).name, folder)

    def test_resolved_folders_contain_documents(self):
        for company in load_companies():
            documents = list(company.corpus_dir.rglob("*.md"))
            self.assertGreater(len(documents), 100, msg=company.slug)

    def test_unknown_company_raises(self):
        with self.assertRaises(ConfigurationError):
            resolve_corpus_dir("Nonexistent Holdings")


if __name__ == "__main__":
    unittest.main()
