"""Tests for numeric extraction.

A unit error is the cheapest way to score the maximum penalty on a metric, so
these tests pin the exact wordings the four companies use in their filings.
"""

from __future__ import annotations

import unittest

from src.config import Metric, find_company
from src.extract import (
    metric_query,
    metric_search_terms,
    parse_number,
)


class ParseNumberTests(unittest.TestCase):
    def test_plain_integer(self):
        self.assertEqual(parse_number("1,773").value, 1773.0)

    def test_currency_and_billions_scale_to_millions(self):
        parsed = parse_number("$41.8 billion")
        self.assertEqual(parsed.value, 41.8)
        self.assertEqual(parsed.currency, "$")
        self.assertEqual(parsed.as_millions(), 41800.0)

    def test_per_share_amount_is_not_scaled(self):
        parsed = parse_number("$3.43")
        self.assertEqual(parsed.value, 3.43)
        self.assertIsNone(parsed.scale)

    def test_percentage_is_recognised(self):
        parsed = parse_number("0.6%")
        self.assertTrue(parsed.is_percent)
        self.assertEqual(parsed.as_percentage_points(), 0.6)
        self.assertIsNone(parsed.as_millions())

    def test_accounting_negative_in_brackets(self):
        # Hays reports declines as (5)%.
        parsed = parse_number("(5)%")
        self.assertEqual(parsed.as_percentage_points(), -5.0)

    def test_leading_minus_is_honoured(self):
        parsed = parse_number("-150 bps")
        self.assertTrue(parsed.negative)
        self.assertEqual(parsed.as_percentage_points(), -1.5)

    def test_basis_points_convert_to_percentage_points(self):
        self.assertEqual(parse_number("100 bps").as_percentage_points(), 1.0)

    def test_pence_is_flagged(self):
        parsed = parse_number("6.2 pence")
        self.assertTrue(parsed.is_pence)
        self.assertEqual(parsed.value, 6.2)

    def test_bare_table_figure_is_treated_as_millions(self):
        self.assertEqual(parse_number("45,210").as_millions(), 45210.0)

    def test_percentages_never_convert_to_money(self):
        self.assertIsNone(parse_number("12.8%").as_millions())

    def test_unparseable_text_returns_none(self):
        self.assertIsNone(parse_number("no figures here"))


class MetricVocabularyTests(unittest.TestCase):
    def test_eps_expands_to_the_phrases_filings_use(self):
        terms = [t.lower() for t in metric_search_terms(Metric("Adjusted diluted EPS", "USD / share"))]
        self.assertIn("adjusted diluted eps", terms)
        self.assertTrue(any("earnings per share" in term for term in terms))

    def test_qualifier_stays_attached(self):
        # "adjusted diluted EPS" and "diluted EPS" are different numbers.
        terms = [t.lower() for t in metric_search_terms(Metric("Adjusted diluted EPS", "USD / share"))]
        self.assertTrue(any(term.startswith("adjusted") for term in terms))

    def test_pre_exceptional_qualifier_is_kept(self):
        terms = [
            t.lower()
            for t in metric_search_terms(Metric("Pre-exceptional operating profit", "GBPm"))
        ]
        self.assertTrue(any("pre-exceptional" in term for term in terms))

    def test_comparable_sales_gets_retail_synonyms(self):
        terms = [
            t.lower()
            for t in metric_search_terms(Metric("Comparable sales, total company", "%"))
        ]
        self.assertTrue(any("comp sales" in term for term in terms))

    def test_every_challenge_metric_produces_terms(self):
        for slug in ("HD", "ADI", "HAS", "DE"):
            company = find_company(slug)
            for metric in company.metrics:
                self.assertTrue(metric_search_terms(metric), msg=f"{slug} {metric.label}")

    def test_query_includes_the_reporting_period(self):
        company = find_company("HD")
        query = metric_query(company, company.metrics[0])
        self.assertIn("2026", query)


if __name__ == "__main__":
    unittest.main()
