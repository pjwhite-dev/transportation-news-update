from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from coverage_history import (
    annotate_previous_coverage,
    filter_previously_covered,
    find_previous_coverage,
    load_published_history,
)
import news_engine


def story(title: str, url: str, **extra: object) -> dict:
    return {
        "id": title,
        "title": title,
        "url": url,
        "section": "UAS and Drones",
        "source": "Example News",
        "summary": "",
        **extra,
    }


class CoverageHistoryTests(unittest.TestCase):
    def test_repeat_topic_is_suppressed_even_when_url_changes(self) -> None:
        previous = story(
            "FAA proposes Part 108 BVLOS drone operations rule",
            "https://example.com/earlier",
            coverage_date="2026-09-01",
        )
        current = story(
            "FAA Part 108 BVLOS drone operations rule draws industry comments",
            "https://example.com/today",
        )

        included, suppressed = filter_previously_covered([current], [previous])

        self.assertEqual(included, [])
        self.assertEqual(len(suppressed), 1)
        self.assertEqual(
            suppressed[0]["previous_coverage"]["title"], previous["title"]
        )

    def test_concrete_later_milestone_is_kept(self) -> None:
        previous = story(
            "Archer plans Dallas air taxi service and operating network",
            "https://example.com/plans",
        )
        current = story(
            "Archer begins operations for Dallas air taxi service network",
            "https://example.com/launch",
        )

        included, suppressed = filter_previously_covered([current], [previous])

        self.assertEqual(suppressed, [])
        self.assertEqual(len(included), 1)
        self.assertEqual(
            included[0]["noteworthy_new_development"], "operational launch"
        )

    def test_required_supplemental_link_is_never_silently_removed(self) -> None:
        previous = story(
            "FAA proposes Part 108 BVLOS drone operations rule",
            "https://example.com/earlier",
        )
        current = story(
            "FAA Part 108 BVLOS drone operations rule draws industry comments",
            "https://example.com/today",
            required_include=True,
        )

        included, suppressed = filter_previously_covered([current], [previous])

        self.assertEqual(suppressed, [])
        self.assertEqual(len(included), 1)
        self.assertTrue(included[0]["previously_covered"])

    def test_same_day_matching_can_ignore_section_for_consolidation(self) -> None:
        previous = story(
            "Pony AI and Verne launch autonomous taxi service in Zagreb",
            "https://example.com/first",
            section="Autonomous Vehicles",
        )
        current = story(
            "Pony AI and Verne launch autonomous taxi service in Zagreb",
            "https://example.com/second",
            section="International",
        )

        default_match, _, _ = find_previous_coverage(current, [previous])
        same_day_match, _, _ = find_previous_coverage(
            current,
            [previous],
            require_same_section=False,
        )

        self.assertIsNone(default_match)
        self.assertIsNotNone(same_day_match)

    def test_load_published_history_obeys_date_and_lookback(self) -> None:
        with TemporaryDirectory() as temporary:
            archive = Path(temporary)
            for day in ("2026-08-01", "2026-09-08", "2026-09-10"):
                payload = {
                    "window_end": f"{day}T04:15:00-04:00",
                    "sections": {
                        "UAS and Drones": [
                            story(f"Story from {day}", f"https://example.com/{day}")
                        ]
                    },
                }
                (archive / f"{day}.json").write_text(json.dumps(payload))

            loaded = load_published_history(
                archive, date(2026, 9, 10), lookback_days=10
            )

        self.assertEqual([item["coverage_date"] for item in loaded], ["2026-09-08"])

    def test_validation_excludes_stale_automated_cluster(self) -> None:
        record = story(
            "FAA Part 108 BVLOS drone operations rule draws industry comments",
            "https://example.com/today",
            previously_covered=True,
            noteworthy_new_development="",
            search_section="UAS and Drones",
            origin="Google News RSS",
        )
        analysis = {
            "what_to_watch": [],
            "clusters": [
                {
                    "cluster_id": "repeat",
                    "article_ids": [record["id"]],
                    "primary_article_id": record["id"],
                    "section": "UAS and Drones",
                    "relevant": True,
                    "importance": 5,
                    "canonical_title": record["title"],
                    "summary": "No new milestone.",
                    "innovative_uas_use": "",
                    "is_administration_win": False,
                    "win_event_within_window": False,
                    "win_direct_administration_nexus": False,
                    "win_concrete_american_benefit": False,
                    "win_foreign_company_expansion_only": False,
                    "eo_number": "",
                    "eo_section": "",
                    "win_explanation": "",
                    "confidence": "high",
                    "exclude_reason": "",
                }
            ],
        }

        validated = news_engine.validate_analysis(analysis, [record])

        self.assertFalse(validated["clusters"][0]["relevant"])
        self.assertIn("Previously covered", validated["clusters"][0]["exclude_reason"])


if __name__ == "__main__":
    unittest.main()
