from __future__ import annotations

import copy
import unittest

import automated_briefing as ab
import validation_gates as gates
from tests.test_validation_gates import NOW, failed_checks, good_briefing


def gate_report(briefing: dict, **kwargs) -> dict:
    return gates.run_gates(briefing, now=NOW, **kwargs)


class RepairStoryFailuresTests(unittest.TestCase):
    def test_duplicate_story_is_dropped_and_the_rest_kept(self) -> None:
        briefing = good_briefing()
        briefing["sections"]["UAS / Drones"].append(copy.deepcopy(briefing["sections"]["UAS / Drones"][0]))
        report = gate_report(briefing)
        self.assertIn("duplicates", failed_checks(report))
        removed = gates.repair_story_failures(briefing, report)
        self.assertEqual([r["reason"] for r in removed], ["repaired: duplicate of an earlier story"])
        self.assertEqual(len(briefing["sections"]["UAS / Drones"]), 2)
        self.assertNotIn("duplicates", failed_checks(gate_report(briefing)))

    def test_headline_echo_is_blanked_not_dropped(self) -> None:
        briefing = good_briefing()
        briefing["sections"]["Autonomous Vehicles"][0]["summary"] = "NHTSA grants Zoox exemption."
        removed = gates.repair_story_failures(briefing, gate_report(briefing))
        self.assertEqual(briefing["sections"]["Autonomous Vehicles"][0]["summary"], "")
        self.assertEqual(len(removed), 1)

    def test_bad_also_covered_links_are_dropped_and_the_story_stays(self) -> None:
        briefing = good_briefing()
        story = briefing["sections"]["UAS / Drones"][0]
        story["also_covered"] = [
            {"title": "Wrapper", "url": "https://news.google.com/rss/articles/CBMi123"},
            {"title": "Fine", "url": "https://ok.example.com/news/fine"},
        ]
        report = gate_report(briefing)
        self.assertIn("links", failed_checks(report))
        gates.repair_story_failures(briefing, report, lambda url: 200)
        self.assertEqual(story["also_covered"], [{"title": "Fine", "url": "https://ok.example.com/news/fine"}])

    def test_edition_level_failures_are_never_repaired(self) -> None:
        briefing = good_briefing()
        briefing["window_end"] = "2026-10-24T04:30:00-04:00"
        report = gate_report(briefing, today=NOW.date())
        self.assertIn("freshness", failed_checks(report))
        self.assertEqual(gates.repair_story_failures(briefing, report), [])


class RepairLoopTests(unittest.TestCase):
    def test_loop_repairs_then_still_enforces_the_story_floor(self) -> None:
        briefing = good_briefing()
        briefing["sections"]["UAS / Drones"].append(copy.deepcopy(briefing["sections"]["UAS / Drones"][0]))
        fixed = ab.repair_briefing(briefing, today=None, check_links=False, minimum_stories=2)
        self.assertEqual(sum(len(v) for v in fixed["sections"].values()), 3)
        with self.assertRaises(ValueError):
            ab.repair_briefing(good_briefing(), today=None, check_links=False, minimum_stories=50)


if __name__ == "__main__":
    unittest.main()
