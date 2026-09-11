from __future__ import annotations

import json
from pathlib import Path
import unittest


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / "n8n"
    / "workflows"
    / "transportation-news-local.json"
)


class N8nWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        self.nodes = {node["name"]: node for node in self.workflow["nodes"]}

    def test_workflow_is_active_and_fallback_runs_at_one_eastern(self) -> None:
        schedule = self.nodes["Weekday 1 PM fallback"]
        interval = schedule["parameters"]["rule"]["interval"]

        self.assertTrue(self.workflow["active"])
        self.assertEqual(self.workflow["settings"]["timezone"], "America/New_York")
        self.assertEqual(interval[0]["expression"], "0 13 * * 1-5")

    def test_successful_paths_share_a_persisted_daily_guard(self) -> None:
        connections = self.workflow["connections"]

        self.assertIn("lastSuccessfulPublicationDate", self.nodes[
            "Skip if already published today"
        ]["parameters"]["jsCode"])
        self.assertIn("lastSuccessfulPublicationDate", self.nodes[
            "Skip fallback if already published today"
        ]["parameters"]["jsCode"])
        self.assertEqual(
            connections["Run validated local publication"]["main"][0][0]["node"],
            "Mark daily publication complete",
        )
        self.assertEqual(
            connections["Run scheduled feed-only publication"]["main"][0][0]["node"],
            "Mark daily publication complete",
        )

    def test_gmail_still_polls_every_minute(self) -> None:
        gmail = self.nodes["Supplemental Gmail"]
        poll = gmail["parameters"]["pollTimes"]["item"]

        self.assertEqual(poll, [{"mode": "everyMinute"}])


if __name__ == "__main__":
    unittest.main()
