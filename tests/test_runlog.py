"""Tests for run logging and secret redaction.

The log is committed, uploaded as the clear-run record and shown on screen during
judging, so a credential reaching it would be a real disclosure.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from src.runlog import Redactor, RunLogger


class RedactorTests(unittest.TestCase):
    def test_masks_openai_style_keys(self):
        redactor = Redactor()
        text = redactor.scrub_text("using sk-abcdefghijklmnopqrstuvwxyz012345 now")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", text)
        self.assertIn("[REDACTED]", text)

    def test_masks_anthropic_style_keys(self):
        redactor = Redactor()
        text = redactor.scrub_text("sk-ant-api03-ZZZZZZZZZZZZZZZZZZZZZZZZ")
        self.assertNotIn("ZZZZZZZZZZZZZZZZZZZZZZZZ", text)

    def test_masks_bearer_tokens(self):
        redactor = Redactor()
        text = redactor.scrub_text("Authorization: Bearer abcdefghijklmnopqrstuvwxyz")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", text)

    def test_masks_values_of_secret_named_environment_variables(self):
        secret = "totally-unique-secret-value-12345"
        os.environ["TEST_FAKE_API_KEY"] = secret
        try:
            redactor = Redactor()
            text = redactor.scrub_text(f"connecting with {secret}")
            self.assertNotIn(secret, text)
            self.assertIn("TEST_FAKE_API_KEY", text)
        finally:
            del os.environ["TEST_FAKE_API_KEY"]

    def test_scrubs_nested_structures(self):
        redactor = Redactor()
        payload = {
            "headers": {"authorization": "Bearer abcdefghijklmnopqrstuvwxyz"},
            "notes": ["sk-abcdefghijklmnopqrstuvwxyz012345", 7, None],
        }
        scrubbed = redactor.scrub(payload)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", json.dumps(scrubbed))
        self.assertEqual(scrubbed["notes"][1], 7)
        self.assertIsNone(scrubbed["notes"][2])

    def test_truncates_very_long_strings(self):
        redactor = Redactor()
        scrubbed = redactor.scrub_text("x" * 9000)
        self.assertLess(len(scrubbed), 9000)
        self.assertIn("truncated", scrubbed)

    def test_leaves_ordinary_text_alone(self):
        redactor = Redactor()
        message = "Net sales forecast is 45210 USDm for FY2026Q2"
        self.assertEqual(redactor.scrub_text(message), message)


class RunLoggerTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.log_path = Path(self._temp.name) / "run.jsonl"

    def tearDown(self):
        self._temp.cleanup()

    def _read(self) -> list[dict]:
        lines = self.log_path.read_text(encoding="utf-8").strip().splitlines()
        return [json.loads(line) for line in lines]

    def test_writes_one_json_object_per_line(self):
        with RunLogger("run-1", self.log_path, echo=False) as logger:
            logger.event("alpha", value=1)
            logger.event("beta", value=2)
        records = self._read()
        self.assertEqual([r["type"] for r in records], ["alpha", "beta"])
        self.assertEqual([r["seq"] for r in records], [1, 2])

    def test_every_record_carries_run_id_and_timestamp(self):
        with RunLogger("run-xyz", self.log_path, echo=False) as logger:
            logger.info("hello")
        record = self._read()[0]
        self.assertEqual(record["run_id"], "run-xyz")
        self.assertTrue(record["ts"].endswith("Z"))

    def test_secrets_never_reach_disk(self):
        with RunLogger("run-1", self.log_path, echo=False) as logger:
            logger.event("llm.call", prompt="key is sk-abcdefghijklmnopqrstuvwxyz012345")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", self.log_path.read_text(encoding="utf-8"))

    def test_stage_records_start_end_and_duration(self):
        with RunLogger("run-1", self.log_path, echo=False) as logger:
            with logger.stage("retrieval") as state:
                state["documents"] = 12
        records = self._read()
        self.assertEqual([r["type"] for r in records], ["stage.start", "stage.end"])
        self.assertEqual(records[1]["documents"], 12)
        self.assertIn("duration_s", records[1])

    def test_stage_records_failure_and_reraises(self):
        with self.assertRaises(ValueError):
            with RunLogger("run-1", self.log_path, echo=False) as logger:
                with logger.stage("retrieval"):
                    raise ValueError("boom")
        records = self._read()
        self.assertEqual(records[-1]["type"], "stage.error")
        self.assertEqual(records[-1]["error_type"], "ValueError")

    def test_concurrent_writes_stay_well_formed(self):
        # The four company pipelines share one logger.
        with RunLogger("run-1", self.log_path, echo=False) as logger:
            threads = [
                threading.Thread(target=lambda n=n: [logger.event("tick", worker=n) for _ in range(25)])
                for n in range(4)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        records = self._read()
        self.assertEqual(len(records), 100)
        self.assertEqual(sorted(r["seq"] for r in records), list(range(1, 101)))


if __name__ == "__main__":
    unittest.main()
