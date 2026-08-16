"""Tests for corpus indexing and point-in-time retrieval.

The smoke tests below pin facts that are known to exist in the frozen corpus. If
any of them stops being retrievable, the retriever has regressed and every
downstream forecast is built on worse evidence.

These are retrieval fixtures, not forecasts. Nothing here may be used as a
predicted value.
"""

from __future__ import annotations

import unittest
from datetime import date

from src import asof
from src.asof import AsOfGuard
from src.corpus import CorpusIndex, get_index, is_informative, normalise_text, tokenize
from src.errors import AsOfLeakError, GuardNotConfiguredError

COMPETITION_CUTOFF = date(2026, 8, 16)


def setUpModule() -> None:
    # Built once and shared: a rebuild takes ~35s, a cached load ~1.5s.
    global INDEX
    asof.set_guard(AsOfGuard(COMPETITION_CUTOFF))
    INDEX = get_index()
    asof.set_guard(None)


class TextHandlingTests(unittest.TestCase):
    def test_zero_width_characters_are_stripped(self):
        self.assertEqual(normalise_text("Net​sales﻿"), "Netsales")

    def test_table_scaffolding_is_not_informative(self):
        self.assertFalse(is_informative("| --- | --- | --- |\n| | | |\n| | | |" * 3))

    def test_financial_tables_are_informative(self):
        table = (
            "| Net income attributable to Deere & Company | $ | 1,773 | $ | 1,804 | "
            "| Provision for income taxes | 518 | 539 | 714 | 566 |"
        )
        self.assertTrue(is_informative(table))

    def test_prose_is_informative(self):
        self.assertTrue(
            is_informative(
                "The Home Depot today reported sales of $41.8 billion for the first "
                "quarter of fiscal 2026, an increase of 4.8 percent."
            )
        )

    def test_tokenizer_keeps_numbers_and_drops_stopwords(self):
        tokens = tokenize("The revenue of the company was 3.9 billion")
        self.assertIn("revenue", tokens)
        self.assertIn("3.9", tokens)
        self.assertNotIn("the", tokens)


class IndexShapeTests(unittest.TestCase):
    def test_every_corpus_document_is_indexed(self):
        stats = INDEX.stats()
        self.assertEqual(stats["documents"], 1139)

    def test_document_type_counts_match_the_published_index(self):
        by_type = INDEX.stats()["by_type"]
        self.assertEqual(by_type["FILING"], 507)
        self.assertEqual(by_type["CALL_TRANSCRIPT"], 538)
        self.assertEqual(by_type["SLIDE"], 94)

    def test_documents_are_attributed_to_the_right_companies(self):
        by_company = INDEX.stats()["by_company"]
        self.assertEqual(by_company, {"ADI": 271, "DE": 310, "HAS": 239, "HD": 319})

    def test_every_document_has_a_publication_date(self):
        undated = [
            document.rel_path
            for document in INDEX.documents.values()
            if document.published_at is None
        ]
        self.assertEqual(undated, [], msg="undated documents would be blocked by the guard")


class GuardEnforcementTests(unittest.TestCase):
    """Retrieval must be impossible without a configured cutoff."""

    def tearDown(self):
        asof.set_guard(None)

    def test_search_without_a_guard_raises(self):
        asof.set_guard(None)
        with self.assertRaises(GuardNotConfiguredError):
            INDEX.search("revenue", company="ADI")

    def test_visible_documents_without_a_guard_raises(self):
        asof.set_guard(None)
        with self.assertRaises(GuardNotConfiguredError):
            INDEX.visible_documents(company="ADI")

    def test_reading_a_future_document_raises(self):
        asof.set_guard(AsOfGuard(date(2026, 1, 1)))
        future = next(
            document
            for document in INDEX.documents.values()
            if document.published_at and document.published_at > date(2026, 1, 1)
        )
        with self.assertRaises(AsOfLeakError):
            INDEX.get_document(future.doc_id)

    def test_search_never_returns_documents_past_the_cutoff(self):
        cutoff = date(2026, 5, 1)
        asof.set_guard(AsOfGuard(cutoff))
        for company in ("HD", "ADI", "HAS", "DE"):
            hits = INDEX.search("revenue earnings guidance outlook", company=company, limit=20)
            self.assertTrue(hits, msg=f"no hits for {company}")
            for hit in hits:
                self.assertLessEqual(hit.document.published_at, cutoff, msg=hit.document.rel_path)

    def test_adi_q2_release_is_invisible_before_it_was_published(self):
        # Published 2026-05-20; a backtest cutting off in April must not see it.
        asof.set_guard(AsOfGuard(date(2026, 4, 30)))
        hits = INDEX.search("third quarter fiscal 2026 forecasting revenue", company="ADI", limit=25)
        self.assertNotIn(
            "2026-05-20__adi-us-20260520-q2-8k-2__1040614",
            {hit.document.doc_id for hit in hits},
        )

    def test_guard_counts_what_it_blocked(self):
        guard = AsOfGuard(date(2020, 1, 1))
        asof.set_guard(guard)
        INDEX.search("revenue", company="HD", limit=5)
        self.assertGreater(guard.stats.blocked, 0)


class RetrievalSmokeTests(unittest.TestCase):
    """Known facts that must remain retrievable at the competition cutoff."""

    def setUp(self):
        asof.set_guard(AsOfGuard(COMPETITION_CUTOFF))

    def tearDown(self):
        asof.set_guard(None)

    def _joined(self, query: str, company: str, limit: int = 6, latest: bool = False) -> str:
        finder = INDEX.search_latest if latest else INDEX.search
        hits = finder(query, company=company, limit=limit)
        self.assertTrue(hits, msg=f"no hits for {company}: {query}")
        return "\n".join(hit.chunk.text for hit in hits)

    def test_adi_third_quarter_guidance_is_retrievable(self):
        text = self._joined(
            "third quarter fiscal 2026 outlook forecasting revenue adjusted EPS", "ADI"
        )
        self.assertIn("$3.9 billion", text)
        self.assertIn("$3.30", text)

    def test_deere_full_year_guidance_is_retrievable(self):
        text = self._joined("net income attributable to Deere fiscal 2026 forecasted range", "DE")
        self.assertIn("$4.5 billion", text)
        self.assertIn("$5.0 billion", text)

    def test_deere_second_quarter_actuals_are_retrievable(self):
        text = self._joined("Deere reports second quarter net income per share", "DE")
        self.assertIn("1.773", text)

    def test_home_depot_first_quarter_actuals_are_retrievable(self):
        text = self._joined(
            "first quarter fiscal 2026 comparable sales adjusted diluted earnings per share", "HD"
        )
        self.assertIn("$41.8 billion", text)
        self.assertIn("3.43", text)

    def test_home_depot_fiscal_2026_guidance_is_retrievable(self):
        text = self._joined("fiscal 2026 guidance total sales growth comparable sales", "HD")
        self.assertIn("2.5%", text)

    def test_hays_fourth_quarter_trading_update_is_retrievable(self):
        text = self._joined(
            "net fees quarterly trading update Germany like-for-like", "HAS", latest=True
        )
        self.assertIn("Germany", text)

    def test_hays_year_end_is_reachable_because_fy2026_has_closed(self):
        # Hays' FY2026 ended 30 June 2026 and was updated on 10 July 2026, so the
        # newest Hays material sits inside the forecast period, not before it.
        asof.set_guard(AsOfGuard(COMPETITION_CUTOFF))
        documents = INDEX.visible_documents(company="HAS")
        self.assertGreaterEqual(max(d.published_at for d in documents), date(2026, 7, 10))


class SearchBehaviourTests(unittest.TestCase):
    def setUp(self):
        asof.set_guard(AsOfGuard(COMPETITION_CUTOFF))

    def tearDown(self):
        asof.set_guard(None)

    def test_company_filter_is_respected(self):
        hits = INDEX.search("revenue", company="ADI", limit=15)
        self.assertTrue(all(hit.document.company_slug == "ADI" for hit in hits))

    def test_document_type_filter_is_respected(self):
        hits = INDEX.search("revenue", company="HD", document_types=["FILING"], limit=15)
        self.assertTrue(hits)
        self.assertTrue(all(hit.document.document_type == "FILING" for hit in hits))

    def test_results_are_capped_per_document(self):
        hits = INDEX.search("revenue", company="DE", limit=30, max_per_document=2)
        counts: dict[str, int] = {}
        for hit in hits:
            counts[hit.document.doc_id] = counts.get(hit.document.doc_id, 0) + 1
        self.assertLessEqual(max(counts.values()), 2)

    def test_recency_weighting_prefers_newer_material(self):
        query = "net fees trading update like-for-like"
        weighted = INDEX.search(query, company="HAS", limit=3)
        unweighted = INDEX.search(query, company="HAS", limit=3, recency_halflife_days=None)
        newest_weighted = max(hit.document.published_at for hit in weighted)
        newest_unweighted = max(hit.document.published_at for hit in unweighted)
        self.assertGreaterEqual(newest_weighted, newest_unweighted)

    def test_search_latest_returns_recent_and_relevant_material(self):
        hits = INDEX.search_latest("quarterly trading update net fees", company="HAS", limit=4)
        self.assertTrue(hits)
        self.assertGreaterEqual(max(hit.document.published_at for hit in hits), date(2026, 4, 1))

    def test_empty_query_returns_nothing(self):
        self.assertEqual(INDEX.search("the and of", company="HD"), [])

    def test_hits_serialise_with_full_provenance(self):
        hit = INDEX.search("comparable sales", company="HD", limit=1)[0]
        payload = hit.as_dict()
        for field in ("doc_id", "path", "published_at", "document_type", "period", "excerpt"):
            self.assertIn(field, payload)
        self.assertTrue(payload["path"].startswith("challenge/offline-data/"))

    def test_visible_documents_are_ordered_newest_first(self):
        documents = INDEX.visible_documents(company="DE")
        dates = [document.published_at for document in documents]
        self.assertEqual(dates, sorted(dates, reverse=True))


class IndexPersistenceTests(unittest.TestCase):
    def test_fingerprint_is_stable_between_calls(self):
        self.assertEqual(CorpusIndex.fingerprint(), CorpusIndex.fingerprint())


if __name__ == "__main__":
    unittest.main()
