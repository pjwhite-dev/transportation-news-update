from __future__ import annotations

import copy
import json
import unittest
from datetime import date, datetime
from pathlib import Path

import validation_gates as gates
from validation_gates import EASTERN, REQUIRED_RINS

KNOWN_BAD = Path(__file__).parent / "regression" / "known_bad"
NOW = datetime(2026, 10, 26, 6, 30, tzinfo=EASTERN)


def good_briefing() -> dict:
    return {
        "briefing_date": "2026-10-26",
        "window_start": "2026-10-23T04:30:00-04:00",
        "window_end": "2026-10-26T04:30:00-04:00",
        "executive_summary": (
            "The FAA finalized a rule that lets operators fly drones beyond visual line of sight."
        ),
        "what_to_watch": ["Comments on the NHTSA exemption petition close on November 9."],
        "sections": {
            "UAS / Drones": [
                {
                    "title": "FAA finalizes beyond-visual-line-of-sight drone rule",
                    "summary": "The agency published Part 108 on Monday, allowing routine remote operations.",
                    "url": "https://www.faa.gov/newsroom/part108",
                },
                {
                    "title": "Zipline expands delivery network to Dallas suburbs",
                    "summary": "Zipline added 12 stores to its network this week.",
                    "url": "https://www.example-news.com/zipline-dallas?utm_source=x",
                },
            ],
            "Autonomous Vehicles": [
                {
                    "title": "NHTSA grants Zoox exemption",
                    "summary": "Regulators approved a temporary exemption for 2,500 vehicles.",
                    "url": "https://www.nhtsa.gov/zoox",
                }
            ],
        },
        "regulatory_tracker": [
            {
                "rin": rin,
                "source_url": f"https://www.federalregister.gov/d/{rin}",
                "status": "Pending final rule",
                "verified_at": "2026-10-26T04:45:00-04:00",
            }
            for rin in REQUIRED_RINS
        ],
        "provenance": {
            "story_analysis": {"provider": "ollama", "model": "qwen3.6:27b-q4_K_M"},
            "executive_summary": {"provider": "ollama", "model": "qwen3.6:27b-q4_K_M"},
            "fallback_text_used": False,
        },
    }


def failed_checks(report: dict) -> set[str]:
    return {check["check"] for check in report["checks"] if not check["passed"]}


class GoodEditionTests(unittest.TestCase):
    def test_clean_edition_passes_every_gate(self) -> None:
        report = gates.run_gates(good_briefing(), today=date(2026, 10, 26), now=NOW)
        self.assertEqual(failed_checks(report), set(), gates.failing_checks(report))
        self.assertTrue(report["passed"])


class FreshnessTests(unittest.TestCase):
    def test_stale_edition_date_fails(self) -> None:
        report = gates.run_gates(good_briefing(), today=date(2026, 10, 27), now=NOW)
        self.assertIn("freshness", failed_checks(report))

    def test_window_must_start_at_previous_cutoff(self) -> None:
        cutoff = datetime(2026, 10, 23, 4, 30, tzinfo=EASTERN)
        report = gates.run_gates(good_briefing(), now=NOW, previous_cutoff=cutoff)
        self.assertNotIn("freshness", failed_checks(report))
        briefing = good_briefing()
        briefing["window_start"] = "2026-10-25T04:30:00-04:00"  # skips the weekend
        report = gates.run_gates(briefing, now=NOW, previous_cutoff=cutoff)
        self.assertIn("freshness", failed_checks(report))


class TemplateAndEchoTests(unittest.TestCase):
    def test_fallback_templates_fail_everywhere(self) -> None:
        for field, mutate in {
            "exec": lambda b: b.update(executive_summary="Key developments include A; B; and C."),
            "story": lambda b: b["sections"]["UAS / Drones"][0].update(summary="Source headline: FAA rule"),
            "watch": lambda b: b.update(what_to_watch=["Watch for follow-up developments."]),
            "generic": lambda b: b.update(
                executive_summary="Today's briefing covers the most consequential developments in advanced transportation."
            ),
        }.items():
            with self.subTest(field):
                briefing = good_briefing()
                mutate(briefing)
                report = gates.run_gates(briefing, now=NOW)
                self.assertIn("template_text", failed_checks(report))

    def test_headline_echo_fails_and_real_summary_passes(self) -> None:
        briefing = good_briefing()
        story = briefing["sections"]["UAS / Drones"][0]
        story["summary"] = "FAA finalizes beyond visual line of sight drone rule."
        self.assertIn("headline_echo", failed_checks(gates.run_gates(briefing, now=NOW)))
        story["summary"] = "The agency published Part 108 on Monday, allowing routine remote operations."
        self.assertNotIn("headline_echo", failed_checks(gates.run_gates(briefing, now=NOW)))


class DuplicateTests(unittest.TestCase):
    def test_same_canonical_url_fails(self) -> None:
        briefing = good_briefing()
        briefing["sections"]["Autonomous Vehicles"].append(
            {
                "title": "Regulators exempt Zoox robotaxi from steering rules",
                "summary": "Different words entirely.",
                "url": "https://www.nhtsa.gov/zoox/?utm_campaign=y",
            }
        )
        self.assertIn("duplicates", failed_checks(gates.run_gates(briefing, now=NOW)))

    def test_near_identical_title_and_cluster_id_fail(self) -> None:
        briefing = good_briefing()
        briefing["sections"]["Military"] = [
            {"title": "Hegseth, Guyanese President Establish Enhanced Military Partnership", "summary": "x y z a", "url": "https://a.mil/1"},
            {"title": "Hegseth, President of Guyana Establish Enhanced Military Partnership", "summary": "x y z a", "url": "https://b.mil/2"},
        ]
        self.assertIn("duplicates", failed_checks(gates.run_gates(briefing, now=NOW)))
        briefing = good_briefing()
        for story in briefing["sections"]["UAS / Drones"]:
            story["cluster_id"] = "c1"
        self.assertIn("duplicates", failed_checks(gates.run_gates(briefing, now=NOW)))

    def test_distinct_stories_are_not_duplicates(self) -> None:
        self.assertNotIn("duplicates", failed_checks(gates.run_gates(good_briefing(), now=NOW)))


class LinkTests(unittest.TestCase):
    def test_aggregator_wrappers_fail(self) -> None:
        for url in (
            "https://news.google.com/rss/articles/CBMiabc?oc=5",
            "https://www.google.com/url?q=https://x.com/a",
        ):
            with self.subTest(url):
                briefing = good_briefing()
                briefing["sections"]["UAS / Drones"][1]["url"] = url
                self.assertIn("links", failed_checks(gates.run_gates(briefing, now=NOW)))

    def test_non_200_fails_when_checked(self) -> None:
        report = gates.run_gates(good_briefing(), now=NOW, fetch_status=lambda url: 404)
        self.assertIn("links", failed_checks(report))
        self.assertIn("tracker", failed_checks(report))
        report = gates.run_gates(good_briefing(), now=NOW, fetch_status=lambda url: 200)
        self.assertEqual(failed_checks(report), set())


class TrackerTests(unittest.TestCase):
    def test_missing_required_rin_fails(self) -> None:
        briefing = good_briefing()
        briefing["regulatory_tracker"] = [
            item for item in briefing["regulatory_tracker"] if item["rin"] != "2126-AC17"
        ]
        report = gates.run_gates(briefing, now=NOW)
        self.assertIn("tracker", failed_checks(report))
        self.assertTrue(any("2126-AC17" in line for line in gates.failing_checks(report)))

    def test_non_official_link_stale_verification_and_unsupported_final_fail(self) -> None:
        briefing = good_briefing()
        briefing["regulatory_tracker"][0]["source_url"] = "https://blog.example.com/rule"
        self.assertIn("tracker", failed_checks(gates.run_gates(briefing, now=NOW)))
        briefing = good_briefing()
        briefing["regulatory_tracker"][1]["verified_at"] = ""
        self.assertIn("tracker", failed_checks(gates.run_gates(briefing, now=NOW)))
        briefing = good_briefing()
        briefing["regulatory_tracker"][1]["verified_at"] = "2026-10-24T04:45:00-04:00"
        report = gates.run_gates(briefing, now=NOW)
        self.assertNotIn("tracker", failed_checks(report))
        tracker_check = next(c for c in report["checks"] if c["check"] == "tracker")
        self.assertTrue(any("verification delayed" in w for w in tracker_check["warnings"]))
        briefing = good_briefing()
        briefing["regulatory_tracker"][2]["status"] = "Final rule"
        self.assertIn("tracker", failed_checks(gates.run_gates(briefing, now=NOW)))
        briefing["regulatory_tracker"][2]["final_rule_document"] = "2026-12345"
        self.assertNotIn("tracker", failed_checks(gates.run_gates(briefing, now=NOW)))


class ProvenanceAndSchemaTests(unittest.TestCase):
    def test_missing_provenance_or_model_fails(self) -> None:
        briefing = good_briefing()
        del briefing["provenance"]
        self.assertIn("provenance", failed_checks(gates.run_gates(briefing, now=NOW)))
        briefing = good_briefing()
        briefing["provenance"]["executive_summary"]["model"] = ""
        self.assertIn("provenance", failed_checks(gates.run_gates(briefing, now=NOW)))
        briefing = good_briefing()
        briefing["provenance"]["fallback_text_used"] = True
        self.assertIn("provenance", failed_checks(gates.run_gates(briefing, now=NOW)))

    def test_empty_sections_fail_schema(self) -> None:
        briefing = good_briefing()
        briefing["sections"] = {"UAS / Drones": []}
        self.assertIn("schema", failed_checks(gates.run_gates(briefing, now=NOW)))

    def test_unknown_status_class_fails_and_required_flag_enforced(self) -> None:
        briefing = good_briefing()
        briefing["sections"]["UAS / Drones"][0]["status_class"] = "rumor"
        self.assertIn("relevance", failed_checks(gates.run_gates(briefing, now=NOW)))
        report = gates.run_gates(good_briefing(), now=NOW, require_status_class=True)
        self.assertIn("relevance", failed_checks(report))


class KnownBadEditionTests(unittest.TestCase):
    """Editions that actually shipped with fallback text must always fail."""

    def test_october_21_and_22_always_fail(self) -> None:
        for name in ("2026-10-21.json", "2026-10-22.json"):
            with self.subTest(name):
                briefing = json.loads((KNOWN_BAD / name).read_text(encoding="utf-8"))
                report = gates.run_gates(briefing, now=NOW)
                self.assertFalse(report["passed"])
                self.assertTrue(
                    {"template_text", "headline_echo", "links", "tracker"} <= failed_checks(report),
                    failed_checks(report),
                )

    def test_synthetic_corruptions_of_a_good_edition_fail(self) -> None:
        corruptions = {
            "template_summary": lambda b: b["sections"]["UAS / Drones"][0].update(summary="Source headline: " + b["sections"]["UAS / Drones"][0]["title"]),
            "empty_exec": lambda b: b.update(executive_summary=""),
            "no_tracker": lambda b: b.update(regulatory_tracker=[]),
            "wrong_date": lambda b: b.update(briefing_date="2026-10-20", window_end="2026-10-20T04:30:00-04:00"),
        }
        for name, corrupt in corruptions.items():
            with self.subTest(name):
                briefing = copy.deepcopy(good_briefing())
                corrupt(briefing)
                report = gates.run_gates(briefing, today=date(2026, 10, 26), now=NOW)
                self.assertFalse(report["passed"])


if __name__ == "__main__":
    unittest.main()
