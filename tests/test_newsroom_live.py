"""The live dashboard feed: best effort, never in the way of an edition."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import newsroom_dashboard
import newsroom_live
from qa_report import QAReport


class LiveFeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"NEWSROOM_LIVE_DIR": self.dir.name, "NEWSROOM_LIVE": "on"})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.dir.cleanup()

    def events(self) -> list[dict]:
        path = Path(self.dir.name) / "events.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_a_qa_drop_becomes_a_stamp_on_the_right_desk(self) -> None:
        report = QAReport("2026-10-29")
        report.add("edition-cap", "Lessons from Africa's Drone Wars", "cut to keep the edition to 25 stories")
        report.extend([{"stage": "article-check", "title": "Spain Technology Newswire", "url": "https://x", "reason": "no date"}])
        stamps = [(e["desk"], e["stamp"]) for e in self.events() if e["kind"] == "stamp"]
        self.assertEqual(stamps, [("layout", "CUT FOR SPACE"), ("factcheck", "SPIKED")])

    def test_model_output_is_streamed_with_its_purpose_and_subject(self) -> None:
        with newsroom_live.purpose("same_event"):
            ink = newsroom_live.Ink("gemma4:31b", subject="Spain mourns after latest train tragedy kills 68")
        ink.write('{"same_event": true, ')
        ink.close()
        kinds = [e["kind"] for e in self.events()]
        self.assertEqual(kinds, ["ink-start", "ink", "ink-end"])
        start = self.events()[0]
        self.assertEqual((start["desk"], start["label"]), ("standards", "Is this the same story?"))
        self.assertIn("Spain", start["subject"])

    def test_subject_is_the_headline_or_the_batch_size(self) -> None:
        self.assertEqual(newsroom_live.subject_from([{"role": "user", "content": "REPORT A\nHeadline: FRA Announces $2.3B\nSource: x"}]),
                         "FRA Announces $2.3B")
        batch = json.dumps([{"id": "a"}, {"id": "b"}, {"id": "c"}])
        self.assertEqual(newsroom_live.subject_from([{"role": "user", "content": batch}]), "A batch of 3 candidate stories")

    def test_an_unwritable_feed_never_raises(self) -> None:
        with patch.dict(os.environ, {"NEWSROOM_LIVE_DIR": str(Path(self.dir.name) / "file.txt" / "nope")}):
            (Path(self.dir.name) / "file.txt").write_text("x")
            newsroom_live.emit("desk", desk="wire", text="still fine")
            newsroom_live.run_start("rehearsal")

    def test_unit_tests_stay_off_the_dashboard_by_default(self) -> None:
        with patch.dict(os.environ, {"NEWSROOM_LIVE": ""}):
            newsroom_live.emit("desk", desk="wire", text="from a test")
        self.assertEqual(self.events(), [])


class DashboardServerTests(unittest.TestCase):
    def test_a_page_that_connects_mid_run_gets_only_the_current_run(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.jsonl"
            lines = [{"kind": "run-start", "mode": "edition"}, {"kind": "desk", "desk": "wire"}, {"kind": "run-end", "status": "ok"},
                     {"kind": "run-start", "mode": "rehearsal"}, {"kind": "desk", "desk": "factcheck"}]
            path.write_text("".join(json.dumps(e) + "\n" for e in lines), encoding="utf-8")
            replay, offset = newsroom_dashboard.read_events_from_last_run(path)
            self.assertEqual([json.loads(line)["kind"] for line in replay], ["run-start", "desk"])
            self.assertEqual(json.loads(replay[0])["mode"], "rehearsal")
            self.assertEqual(offset, path.stat().st_size)

    def test_the_next_edition_is_the_next_weekday_at_0430(self) -> None:
        eastern = ZoneInfo("America/New_York")
        friday_noon = datetime(2026, 10, 30, 12, 0, tzinfo=eastern)
        self.assertEqual(newsroom_dashboard.next_edition(friday_noon), datetime(2026, 11, 2, 4, 30, tzinfo=eastern))
        thursday_early = datetime(2026, 10, 29, 3, 0, tzinfo=eastern)
        self.assertEqual(newsroom_dashboard.next_edition(thursday_early), datetime(2026, 10, 29, 4, 30, tzinfo=eastern))


if __name__ == "__main__":
    unittest.main()
