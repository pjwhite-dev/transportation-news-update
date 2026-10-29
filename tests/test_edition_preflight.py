"""2026-10-28: the 04:30 run and all four retries died in two seconds because the PowerShell cleanup ran
``git checkout -- a b c`` with a path git did not track. The cleanup now lives in edition_preflight.py."""

from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import edition_preflight as pre

ROOT = Path(__file__).resolve().parents[1]


def run(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


class PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        run(self.repo, "init", "-q")
        run(self.repo, "config", "user.email", "t@example.com")
        run(self.repo, "config", "user.name", "t")
        for name in ("tracker/events.jsonl", "tracker/oira.json", "data/market.json", "data/latest_raw_news.json",
                     "data/raw_archive/2026-10-27.json"):
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text("{}\n", encoding="utf-8")
        run(self.repo, "add", "-A")
        run(self.repo, "commit", "-q", "-m", "init")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_untracked_regenerated_path_does_not_abort(self) -> None:
        """tracker/discovered.yaml and data/published_groups.json are not tracked here; the old command aborted."""
        ok, message = pre.preflight(self.repo, "2026-10-28")
        self.assertTrue(ok, message)

    def test_modified_regenerated_files_are_discarded(self) -> None:
        (self.repo / "tracker/oira.json").write_text('{"changed": true}\n', encoding="utf-8")
        (self.repo / "data/latest_raw_news.json").write_text('{"changed": true}\n', encoding="utf-8")
        ok, message = pre.preflight(self.repo, "2026-10-28")
        self.assertTrue(ok, message)
        self.assertEqual((self.repo / "tracker/oira.json").read_text(encoding="utf-8"), "{}\n")

    def test_leftovers_of_a_failed_run_are_tolerated(self) -> None:
        (self.repo / "qa").mkdir()
        (self.repo / "qa/2026-10-28.json").write_text("{}", encoding="utf-8")
        (self.repo / "tracker/discovered.yaml").write_text("x: 1\n", encoding="utf-8")
        (self.repo / "data/raw_archive/2026-10-28.json").write_text("{}", encoding="utf-8")
        ok, message = pre.preflight(self.repo, "2026-10-28")
        self.assertTrue(ok, message)

    def test_todays_tracked_raw_archive_is_discarded_but_older_ones_still_stop_the_run(self) -> None:
        """2026-10-28 evening: a failed run left today's committed raw archive modified; the retry refused."""
        archive = self.repo / "data/raw_archive/2026-10-27.json"
        archive.write_text('{"changed": true}\n', encoding="utf-8")
        ok, message = pre.preflight(self.repo, "2026-10-27")
        self.assertTrue(ok, message)
        self.assertEqual(archive.read_text(encoding="utf-8"), "{}\n")
        archive.write_text('{"changed": true}\n', encoding="utf-8")
        ok, message = pre.preflight(self.repo, "2026-10-28")
        self.assertFalse(ok)
        self.assertIn("2026-10-27.json", message)

    def test_any_other_dirty_file_still_stops_the_run(self) -> None:
        (self.repo / "automated_briefing.py").write_text("print('edited')\n", encoding="utf-8")
        ok, message = pre.preflight(self.repo, "2026-10-28")
        self.assertFalse(ok)
        self.assertIn("repository is not clean", message)
        self.assertIn("automated_briefing.py", message)


class ScriptLintTests(unittest.TestCase):
    def test_pipeline_script_uses_preflight_not_multi_path_checkout(self) -> None:
        text = (ROOT / "scripts" / "run_local_pipeline.ps1").read_text(encoding="utf-8")
        self.assertIn("edition_preflight.py", text)
        multi = re.findall(r"git checkout --\s+\S+\s+\S+", text)
        self.assertEqual(multi, [], "a multi-path git checkout aborts when one path is untracked")


if __name__ == "__main__":
    unittest.main()
