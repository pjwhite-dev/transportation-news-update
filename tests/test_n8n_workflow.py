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

    def test_late_email_can_replace_provisional_feed_only_edition_once(self) -> None:
        connections = self.workflow["connections"]

        self.assertIn("--mode supplemental", self.nodes[
            "Check supplemental publication state"
        ]["parameters"]["command"])
        self.assertIn("--mode any", self.nodes[
            "Check feed publication state"
        ]["parameters"]["command"])
        self.assertIn("READY", self.nodes[
            "Skip if already published today"
        ]["parameters"]["jsCode"])
        self.assertEqual(
            connections["Run validated local publication"]["main"][0][0]["node"],
            "Mark daily publication complete",
        )
        self.assertEqual(
            connections["Run scheduled feed-only publication"]["main"][0][0]["node"],
            "Mark provisional feed-only publication complete",
        )
        self.assertIn("lastSupplementalPublicationDate", self.nodes[
            "Mark daily publication complete"
        ]["parameters"]["jsCode"])
        self.assertNotIn("lastSupplementalPublicationDate", self.nodes[
            "Mark provisional feed-only publication complete"
        ]["parameters"]["jsCode"])

    def test_gmail_search_includes_read_and_archived_messages(self) -> None:
        schedule = self.nodes["Check ETTE every five minutes"]
        gmail = self.nodes["Search read and archived Gmail"]
        self.assertEqual(schedule["parameters"]["rule"]["interval"][0]["expression"], "*/5 * * * 1-5")
        self.assertEqual(gmail["parameters"]["filters"]["readStatus"], "both")
        query = gmail["parameters"]["filters"]["q"]
        self.assertNotIn("is:unread", query)
        self.assertNotIn("in:inbox", query)
        self.assertNotIn("9/11/26", query)
        self.assertIn("from:ette0937@yahoo.com", query)

    def test_gmail_prefers_html_to_preserve_actual_link_targets(self) -> None:
        encoder = self.nodes["Encode supplemental email"]
        code = encoder["parameters"]["jsCode"]

        self.assertLess(code.index("message.html"), code.index("message.text"))

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

    def test_missing_supplemental_file_cannot_publish_feed_only(self) -> None:
        script = PIPELINE_SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn("Supplemental email file is missing; refusing feed-only publication.", script)


if __name__ == "__main__":
    unittest.main()
