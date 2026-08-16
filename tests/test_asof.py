"""Tests for the point-in-time cutoff.

These are the most important tests in the repository. If the guard leaks, the
backtest silently overstates accuracy and the run stops being reproducible.
"""

from __future__ import annotations

import threading
import unittest
from datetime import date, datetime

from src import asof
from src.asof import AsOfGuard, assert_no_leak, parse_published_at
from src.errors import AsOfLeakError, GuardNotConfiguredError


class ParsePublishedAtTests(unittest.TestCase):
    def test_parses_iso_dates(self):
        self.assertEqual(parse_published_at("2026-05-19"), date(2026, 5, 19))

    def test_parses_full_timestamps(self):
        self.assertEqual(parse_published_at("2026-05-19T14:30:00Z"), date(2026, 5, 19))

    def test_passes_through_date_objects(self):
        self.assertEqual(parse_published_at(date(2026, 1, 2)), date(2026, 1, 2))

    def test_unknown_values_become_none(self):
        for value in (None, "", "   ", "not a date", 42):
            self.assertIsNone(parse_published_at(value), msg=repr(value))


class GuardBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.guard = AsOfGuard(date(2026, 8, 16))

    def test_cutoff_is_inclusive(self):
        self.assertTrue(self.guard.is_allowed("2026-08-16"))

    def test_earlier_documents_are_allowed(self):
        self.assertTrue(self.guard.is_allowed("2026-05-19"))

    def test_later_documents_are_blocked(self):
        self.assertFalse(self.guard.is_allowed("2026-08-17"))

    def test_strictly_before_excludes_the_report_itself(self):
        guard = AsOfGuard.strictly_before(date(2026, 5, 19))
        self.assertFalse(guard.is_allowed("2026-05-19"))
        self.assertTrue(guard.is_allowed("2026-05-18"))

    def test_unknown_dates_are_blocked_by_default(self):
        self.assertFalse(self.guard.is_allowed(None))

    def test_unknown_dates_can_be_permitted_explicitly(self):
        lenient = AsOfGuard(date(2026, 8, 16), allow_unknown_dates=True)
        self.assertTrue(lenient.is_allowed(None))

    def test_assert_allowed_raises_on_leak(self):
        with self.assertRaises(AsOfLeakError) as caught:
            self.guard.assert_allowed("2026-09-01", source="filings/future.md")
        self.assertIn("filings/future.md", str(caught.exception))

    def test_rejects_datetime_as_cutoff(self):
        # A datetime would make the inclusive comparison depend on time of day.
        with self.assertRaises(TypeError):
            AsOfGuard(datetime(2026, 8, 16, 12, 0))  # type: ignore[arg-type]


class GuardFilterTests(unittest.TestCase):
    def setUp(self):
        self.guard = AsOfGuard(date(2026, 6, 30))
        self.documents = [
            {"path": "a.md", "published_at": "2026-01-15"},
            {"path": "b.md", "published_at": "2026-06-30"},
            {"path": "c.md", "published_at": "2026-07-10"},
            {"path": "d.md", "published_at": None},
        ]

    def test_filter_keeps_only_permitted_documents(self):
        kept = self.guard.filter(
            self.documents,
            key=lambda doc: doc["published_at"],
            source=lambda doc: doc["path"],
        )
        self.assertEqual([doc["path"] for doc in kept], ["a.md", "b.md"])

    def test_filter_records_statistics(self):
        self.guard.filter(self.documents, key=lambda doc: doc["published_at"])
        stats = self.guard.stats
        self.assertEqual(stats.checked, 4)
        self.assertEqual(stats.allowed, 2)
        self.assertEqual(stats.blocked, 2)
        self.assertEqual(stats.unknown_date, 1)

    def test_blocked_samples_are_captured_for_the_dashboard(self):
        self.guard.filter(
            self.documents,
            key=lambda doc: doc["published_at"],
            source=lambda doc: doc["path"],
        )
        sources = [sample["source"] for sample in self.guard.stats.blocked_samples]
        self.assertIn("c.md", sources)


class ActiveGuardTests(unittest.TestCase):
    def tearDown(self):
        asof.set_guard(None)

    def test_retrieval_without_a_guard_fails_loudly(self):
        asof.set_guard(None)
        with self.assertRaises(GuardNotConfiguredError):
            asof.get_guard()

    def test_global_guard_is_visible(self):
        guard = AsOfGuard(date(2026, 8, 16))
        asof.set_guard(guard)
        self.assertIs(asof.get_guard(), guard)

    def test_using_overrides_then_restores(self):
        base = AsOfGuard(date(2026, 8, 16), label="run")
        replay = AsOfGuard(date(2025, 11, 17), label="backtest")
        asof.set_guard(base)
        with asof.using(replay):
            self.assertIs(asof.get_guard(), replay)
        self.assertIs(asof.get_guard(), base)

    def test_thread_local_guards_do_not_bleed_between_threads(self):
        base = AsOfGuard(date(2026, 8, 16), label="run")
        replay = AsOfGuard(date(2020, 1, 1), label="backtest")
        asof.set_guard(base)
        seen: dict[str, date] = {}

        def worker() -> None:
            with asof.using(replay):
                seen["worker"] = asof.get_guard().as_of

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        self.assertEqual(seen["worker"], date(2020, 1, 1))
        self.assertEqual(asof.get_guard().as_of, date(2026, 8, 16))


class AuditTests(unittest.TestCase):
    """The independent second check over a finished result set."""

    def setUp(self):
        asof.set_guard(AsOfGuard(date(2026, 6, 30)))

    def tearDown(self):
        asof.set_guard(None)

    def test_clean_results_pass(self):
        documents = [{"path": "a.md", "published_at": "2026-01-15"}]
        assert_no_leak(documents, key=lambda doc: doc["published_at"])

    def test_leaked_result_is_caught_even_if_filtering_missed_it(self):
        documents = [{"path": "leak.md", "published_at": "2026-07-01"}]
        with self.assertRaises(AsOfLeakError):
            assert_no_leak(
                documents,
                key=lambda doc: doc["published_at"],
                source=lambda doc: doc["path"],
            )

    def test_missing_date_is_treated_as_a_leak(self):
        documents = [{"path": "undated.md", "published_at": None}]
        with self.assertRaises(AsOfLeakError):
            assert_no_leak(documents, key=lambda doc: doc["published_at"])


if __name__ == "__main__":
    unittest.main()
