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
                    "url": "https://www.nhtsa.gov/press-releases/zoox-exemption-granted",
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
                "url": "https://www.nhtsa.gov/press-releases/zoox-exemption-granted/?utm_campaign=y",
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

    def test_thin_edition_from_many_candidates_fails(self) -> None:
        briefing = good_briefing()
        briefing["candidate_count"] = 295
        self.assertIn("schema", failed_checks(gates.run_gates(briefing, now=NOW)))
        briefing["candidate_count"] = 10
        self.assertNotIn("schema", failed_checks(gates.run_gates(briefing, now=NOW)))

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


def chain_briefing() -> dict:
    briefing = good_briefing()
    urls = [story["url"] for _, _, story in gates.iter_stories(briefing)]
    sentence = "The agency published the rule and operators began flying under it across several states this week"
    paragraphs = []
    for index in range(3):
        links = "".join(f"[{n + 1}]({urls[n]})" for n in range(len(urls)))
        paragraphs.append(" ".join(f"{sentence} {links}." for _ in range(3)))
    briefing["executive_summary"] = "\n\n".join(paragraphs)
    briefing["summary_chain"] = gates.SUMMARY_CHAIN_VERSION
    briefing["provenance"]["executive_summary"].update(
        {"verified_by": "gemma4:31b", "unsupported_remaining": 0, "status_errors_remaining": 0}
    )
    return briefing


class SummaryGateTests(unittest.TestCase):
    def test_verified_chain_summary_passes_and_legacy_edition_is_lenient(self) -> None:
        report = gates.run_gates(chain_briefing(), today=date(2026, 10, 26), now=NOW, require_summary_chain=True)
        self.assertNotIn("summary", failed_checks(report), gates.failing_checks(report))
        legacy = gates.run_gates(good_briefing(), now=NOW)
        self.assertNotIn("summary", failed_checks(legacy))

    def test_new_editions_must_carry_the_chain_marker(self) -> None:
        report = gates.run_gates(good_briefing(), now=NOW, require_summary_chain=True)
        self.assertIn("summary", failed_checks(report))

    def test_each_failure_mode_is_caught(self) -> None:
        def bad(mutate):
            briefing = chain_briefing()
            mutate(briefing)
            return failed_checks(gates.run_gates(briefing, now=NOW, require_summary_chain=True))

        self.assertIn("summary", bad(lambda b: b.update(executive_summary="Too short [1](https://www.faa.gov/newsroom/part108).")))
        self.assertIn("summary", bad(lambda b: b.update(executive_summary=b["executive_summary"].replace("https://www.faa.gov/newsroom/part108", "https://invented.example/x"))))
        self.assertIn("summary", bad(lambda b: b.update(executive_summary=b["executive_summary"] + " A landmark change.")))
        self.assertIn("summary", bad(lambda b: b["provenance"]["executive_summary"].update(verified_by="")))
        self.assertIn("summary", bad(lambda b: b["provenance"]["executive_summary"].update(unsupported_remaining=2)))
        self.assertIn("summary", bad(lambda b: b.update(summary_chain=1)))
        self.assertIn("summary", bad(lambda b: b.update(executive_summary="\n\n".join(b["executive_summary"].split("\n\n")[:1]))))


class PruneTests(unittest.TestCase):
    def test_dead_links_are_removed_and_echo_summaries_blanked_with_a_record(self) -> None:
        briefing = good_briefing()
        briefing["sections"]["UAS / Drones"][1]["url"] = "https://dead.example.com/news/story-one-dead"
        briefing["sections"]["Autonomous Vehicles"][0]["summary"] = "NHTSA grants Zoox exemption."
        statuses = {"https://dead.example.com/news/story-one-dead": 404}
        removed = gates.prune_unusable_stories(briefing, lambda url: statuses.get(url, 200))
        self.assertEqual({r["reason"] for r in removed}, {"dead link (HTTP 404)", "summary restated headline; blanked"})
        self.assertEqual(len(briefing["sections"]["UAS / Drones"]), 1)
        self.assertEqual(briefing["sections"]["Autonomous Vehicles"][0]["summary"], "")

    def test_unreachable_and_5xx_are_removed_but_paywall_and_bot_blocks_stay(self) -> None:
        briefing = good_briefing()
        urls = ["https://a.example.com/news/story-alpha-one", "https://b.example.com/news/story-beta-two"]
        for story, url in zip(briefing["sections"]["UAS / Drones"], urls):
            story["url"] = url
        removed = gates.prune_unusable_stories(briefing, lambda url: None if url == urls[0] else 200)
        self.assertEqual([r["url"] for r in removed], [urls[0]])
        report = gates.run_gates(briefing, now=NOW, fetch_status=lambda url: 402 if url == urls[1] else 200)
        self.assertNotIn("links", failed_checks(report))


    def test_a_dead_also_covered_link_is_dropped_and_the_story_stays(self) -> None:
        briefing = good_briefing()
        story = briefing["sections"]["UAS / Drones"][0]
        dead, live = "https://dead.example.com/news/extra-dead", "https://ok.example.com/news/extra-ok"
        story["also_covered"] = [{"title": "Dead", "url": dead}, {"title": "Live", "url": live}]
        removed = gates.prune_unusable_stories(briefing, lambda url: None if url == dead else 200)
        self.assertEqual([r["reason"] for r in removed], ["dead also-covered link dropped (HTTP None)"])
        self.assertEqual(story["also_covered"], [{"title": "Live", "url": live}])
        report = gates.run_gates(briefing, now=NOW, fetch_status=lambda url: 200)
        self.assertNotIn("links", failed_checks(report))


class NonArticleTests(unittest.TestCase):
    def test_social_quote_topic_and_home_pages_are_removed(self) -> None:
        briefing = good_briefing()
        junk = [
            "https://www.reddit.com/r/JobyAviation/new/",
            "https://www.nytimes.com/topic/subject/predator-drones",
            "https://sg.finance.yahoo.com/quote/EOS.AX/",
            "https://example.com/",
        ]
        briefing["sections"]["Military"] = [
            {"title": f"Junk {i} unrelated words {i}", "summary": "s", "url": url} for i, url in enumerate(junk)
        ]
        removed = gates.prune_unusable_stories(briefing, lambda url: 200)
        self.assertEqual({r["url"] for r in removed}, set(junk))
        self.assertEqual(briefing["sections"]["Military"], [])
        self.assertFalse(gates.is_non_article_url("https://www.faa.gov/newsroom/part108"))


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
