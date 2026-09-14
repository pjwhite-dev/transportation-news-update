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
START_SCRIPT_PATH = WORKFLOW_PATH.parents[2] / "scripts" / "start_n8n.ps1"
PIPELINE_SCRIPT_PATH = WORKFLOW_PATH.parents[2] / "scripts" / "run_local_pipeline.ps1"


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
        query = gmail["parameters"]["filters"]["q"]

        self.assertEqual(poll, [{"mode": "everyMinute"}])
        self.assertEqual(
            query,
            "is:unread newer_than:2d from:ette0937@yahoo.com",
        )
        self.assertNotIn("9/11/26", query)

    def test_gmail_prefers_html_to_preserve_actual_link_targets(self) -> None:
        encoder = self.nodes["Encode supplemental email"]
        code = encoder["parameters"]["jsCode"]

        self.assertLess(code.index("$json.textHtml"), code.index("$json.textPlain"))

    def test_startup_enables_execute_command_but_keeps_file_trigger_blocked(self) -> None:
        script = START_SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn("$env:NODES_EXCLUDE", script)
        self.assertIn("n8n-nodes-base.localFileTrigger", script)
        self.assertNotIn("$env:NODES_EXCLUDE = '[]'", script)
        self.assertIn("$env:N8N_RESTRICT_FILE_ACCESS_TO = $repoRoot", script)

    def test_deployment_check_accepts_human_readable_edition_date(self) -> None:
        script = PIPELINE_SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn('"MMMM d, yyyy"', script)
        self.assertIn("$editionDisplay", script)
        self.assertIn("[regex]::Escape($editionDisplay)", script)


if __name__ == "__main__":
    unittest.main()
