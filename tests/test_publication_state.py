from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.check_publication_state import publication_needed


class PublicationStateTests(unittest.TestCase):
    def test_provisional_feed_only_allows_later_email_once(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "briefing.json"
            payload = {
                "generated_at": "2026-09-15T13:05:00-04:00",
                "validation_status": "passed",
                "supplemental_count": 0,
                "supplemental_accounted_count": 0,
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertFalse(publication_needed(path, "any", "2026-09-15"))
            self.assertTrue(publication_needed(path, "supplemental", "2026-09-15"))

            payload["supplemental_count"] = 46
            payload["supplemental_accounted_count"] = 46
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertFalse(publication_needed(path, "any", "2026-09-15"))
            self.assertFalse(publication_needed(path, "supplemental", "2026-09-15"))

    def test_previous_day_and_incomplete_briefing_allow_run(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "briefing.json"
            payload = {
                "generated_at": "2026-09-14T13:05:00-04:00",
                "validation_status": "passed",
                "supplemental_count": 39,
                "supplemental_accounted_count": 39,
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertTrue(publication_needed(path, "supplemental", "2026-09-15"))
            payload["generated_at"] = "2026-09-15T13:05:00-04:00"
            payload["supplemental_accounted_count"] = 38
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertTrue(publication_needed(path, "supplemental", "2026-09-15"))
