from __future__ import annotations

from datetime import date, datetime
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

import automated_briefing
import news_engine
import public_site
from regulatory_tracker import build_regulatory_tracker


class MasterBriefingTests(TestCase):
    def test_monday_covers_weekend_and_tuesday_covers_one_day(self) -> None:
        eastern = ZoneInfo("America/New_York")
        with patch.object(news_engine, "collect_articles", return_value=([], [])) as collect:
            monday = news_engine.generate_raw_feed(datetime(2026, 9, 21, 4, 15, tzinfo=eastern))
            self.assertEqual(monday["window_start"][:10], "2026-09-18")
            self.assertEqual(monday["window_end"][:10], "2026-09-21")
            tuesday = news_engine.generate_raw_feed(datetime(2026, 9, 22, 4, 15, tzinfo=eastern))
            self.assertEqual(tuesday["window_start"][:10], "2026-09-21")
            self.assertEqual(collect.call_count, 2)

    def test_robotics_and_drone_use_are_classified(self) -> None:
        self.assertEqual(
            news_engine.infer_section({"title": "Warehouse robots begin cargo-handling operations"}),
            "Robotics",
        )
        policy = {
            "title": "Petition to stop agricultural drone deregulation",
            "section": "UAS / Drones",
        }
        ocean = {
            "title": "Ocean drones deployed to monitor Southern Ocean carbon uptake",
            "section": "UAS / Drones",
        }
        self.assertEqual(news_engine.infer_innovative_uas_use(policy), "")
        self.assertEqual(
            news_engine.infer_innovative_uas_use(ocean), "Monitoring ocean carbon uptake"
        )
        story = {"title": "Northern Plains UAS Test Site Flies Medical and Agricultural BVLOS Demos"}
        self.assertEqual(news_engine.infer_section(story), "UAS / Drones")
        self.assertTrue(news_engine.infer_innovative_uas_use(story))

    def test_summary_links_are_limited_to_published_story_urls(self) -> None:
        payload = {
            "executive_summary": "[Flight](https://example.com/flight) and [unknown](https://bad.example/).",
            "sections": {"UAS / Drones": [{"url": "https://example.com/flight"}]},
        }
        rendered = public_site.summary_html(payload)
        self.assertIn('href="https://example.com/flight"', rendered)
        self.assertNotIn("https://bad.example", rendered)

    def test_monday_subject_and_plain_fallback(self) -> None:
        payload = {
            "executive_summary": "A new flight began.",
            "sections": {"Robotics": [{"title": "Robots start cargo operations", "url": "https://example.com/robot", "importance": 8}]},
            "regulatory_tracker": [],
        }
        subject = public_site.email_subject(payload, date(2026, 9, 21))
        self.assertEqual(subject, "Advanced Transportation Daily — 9/21/26 — Weekend + Monday — Robots start cargo operations")
        plain = public_site.plain_text_email(payload, date(2026, 9, 21))
        self.assertIn("https://example.com/robot", plain)
        self.assertTrue(plain.rstrip().endswith("This summary is AI generated."))

    def test_lowercase_summary_fragment_is_omitted(self) -> None:
        payload = {
            "executive_summary": "A valid executive summary.",
            "regulatory_tracker": [],
            "what_to_watch": [],
            "sections": {
                section: []
                for section in news_engine.SECTION_ORDER
            }
        }
        payload["sections"]["Military UAS"] = [
            {
                "title": "Russia receives a drone defense system",
                "summary": "systems.",
                "url": "https://example.com/story",
            }
        ]
        normalized = automated_briefing.normalize_reader_features(payload)
        story = normalized["sections"]["Military UAS"][0]
        self.assertEqual(story["summary"], "")

    def test_tracker_contains_all_four_required_rules(self) -> None:
        tracker = build_regulatory_tracker(date(2026, 9, 20))
        rins = {item.get("rin") for item in tracker}
        self.assertTrue({"2120-AL82", "1652-AA80", "2120-AL33", "2120-AM15"} <= rins)
