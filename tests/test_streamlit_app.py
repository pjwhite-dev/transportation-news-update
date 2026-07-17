from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import unittest

from regulatory_tracker import build_regulatory_tracker
from streamlit.testing.v1 import AppTest


def active_feed_context() -> tuple[str, dict]:
    raw_feed = json.loads(Path("data/latest_raw_news.json").read_text())
    edition_key = datetime.fromisoformat(raw_feed["window_end"]).date().isoformat()
    return edition_key, raw_feed


class FeedOnlyButtonVisibilityTests(unittest.TestCase):
    def load_app(self) -> AppTest:
        app = AppTest.from_file("streamlit_app.py", default_timeout=15).run()
        self.assertEqual([item.value for item in app.exception], [])
        return app

    def test_locked_owner_sees_password_instruction_not_build_button(self) -> None:
        app = self.load_app()
        button_labels = [button.label for button in app.button]
        warnings = [warning.value.casefold() for warning in app.warning]

        self.assertNotIn("Build from Automated Feed Only", button_labels)
        self.assertTrue(
            any(
                "owner password" in warning
                and "button will then appear" in warning
                for warning in warnings
            )
        )

    def test_unlocked_owner_sees_feed_only_build_button(self) -> None:
        app = self.load_app()
        app.session_state["owner_authenticated"] = True
        app.run()

        self.assertEqual([item.value for item in app.exception], [])
        self.assertIn(
            "Build from Automated Feed Only",
            [button.label for button in app.button],
        )

    def test_visible_title_is_preserved_without_old_subtitle(self) -> None:
        app = self.load_app()

        self.assertIn(
            "Advanced Transportation News Update",
            [title.value for title in app.title],
        )
        rendered_text = "\n".join(
            item.value
            for collection in (app.title, app.caption, app.markdown)
            for item in collection
        )
        self.assertNotIn("UAS, C-UAS, and Advanced Transportation", rendered_text)

    def test_status_reports_administration_wins_selected_by_build(self) -> None:
        app = self.load_app()
        edition_key, raw_feed = active_feed_context()
        sections = {
            section: []
            for section in (
                "Trump Administration Wins",
                "Top Developments",
                "UAS and Drones",
                "UAS Security and C-UAS",
                "Military",
                "eVTOL Integration Pilot Program and AAM",
                "Autonomous Vehicles",
                "Other Advanced Transportation",
                "International",
                "Federal Actions",
            )
        }
        sections["Trump Administration Wins"] = [
            {
                "id": "faa-win",
                "title": "FAA accepts new powered-lift standards",
                "summary": "The agency accepted new aviation standards.",
                "source": "FAA",
                "url": "https://www.faa.gov/example",
                "published": raw_feed["window_end"],
                "date_label": "Jul. 17, 2026",
                "section": "eVTOL Integration Pilot Program and AAM",
                "importance": 9,
                "confidence": "high",
                "is_administration_win": True,
                "eo_number": "",
                "eo_name": "",
                "eo_section": "",
                "eo_section_summary": "",
                "win_explanation": "President Trump’s FAA delivered an aviation win.",
                "also_covered": [],
            }
        ]
        app.session_state[f"generated_briefing_{edition_key}"] = {
            "window_start": raw_feed["window_start"],
            "window_end": raw_feed["window_end"],
            "executive_summary": "The FAA accepted new aviation standards.",
            "what_to_watch": [],
            "sections": sections,
            "usage": {},
            "model": "test",
        }
        app.run()

        self.assertIn(
            "**Trump Administration Wins selected by build:** 1",
            [item.value for item in app.markdown],
        )

    def test_tracker_is_visible_and_selectable_before_build(self) -> None:
        app = self.load_app()
        edition_key, raw_feed = active_feed_context()
        tracker = build_regulatory_tracker(
            datetime.fromisoformat(raw_feed["window_end"])
        )

        self.assertIn(
            "Regulatory Deadline Tracker",
            [item.value for item in app.subheader],
        )
        checkbox_keys = {item.key for item in app.checkbox if item.key}
        for item in tracker:
            self.assertIn(
                f"build_{edition_key}_tracker_{item['id']}_include",
                checkbox_keys,
            )

    def test_legacy_briefing_gets_tracker_before_what_to_watch(self) -> None:
        app = self.load_app()
        edition_key, raw_feed = active_feed_context()
        tracker = build_regulatory_tracker(
            datetime.fromisoformat(raw_feed["window_end"])
        )
        sections = {
            section: []
            for section in (
                "Trump Administration Wins",
                "Top Developments",
                "UAS and Drones",
                "UAS Security and C-UAS",
                "Military",
                "eVTOL Integration Pilot Program and AAM",
                "Autonomous Vehicles",
                "Other Advanced Transportation",
                "International",
                "Federal Actions",
            )
        }
        app.session_state["owner_authenticated"] = True
        app.session_state[
            f"build_{edition_key}_tracker_bvlos-part-108_include"
        ] = False
        app.session_state[f"generated_briefing_{edition_key}"] = {
            "window_start": raw_feed["window_start"],
            "window_end": raw_feed["window_end"],
            "executive_summary": "Summary",
            "what_to_watch": ["Watch this"],
            "sections": sections,
            "usage": {},
            "model": "test",
        }
        app.run()

        checkbox_keys = {item.key for item in app.checkbox if item.key}
        for item in tracker:
            self.assertIn(
                f"edit_{edition_key}_tracker_{item['id']}_include",
                checkbox_keys,
            )
        self.assertFalse(
            app.session_state[
                f"edit_{edition_key}_tracker_bvlos-part-108_include"
            ]
        )
        subheaders = [item.value for item in app.subheader]
        self.assertLess(
            subheaders.index("Regulatory Deadline Tracker"),
            subheaders.index("What to Watch"),
        )

    def test_every_story_has_editable_administration_win_controls(self) -> None:
        app = self.load_app()
        edition_key, raw_feed = active_feed_context()
        sections = {
            section: []
            for section in (
                "Trump Administration Wins",
                "Top Developments",
                "UAS and Drones",
                "UAS Security and C-UAS",
                "Military",
                "eVTOL Integration Pilot Program and AAM",
                "Autonomous Vehicles",
                "Other Advanced Transportation",
                "International",
                "Federal Actions",
            )
        }
        sections["Military"] = [
            {
                "id": "military-story",
                "title": "Army tests a new autonomous aircraft",
                "summary": "The service completed a new flight test.",
                "source": "U.S. Army",
                "url": "https://www.army.mil/example",
                "published": "2026-07-15T03:00:00-04:00",
                "date_label": "Jul. 15, 2026",
                "section": "Military",
                "importance": 6,
                "confidence": "high",
                "is_administration_win": False,
                "eo_number": "",
                "eo_name": "",
                "eo_section": "",
                "eo_section_summary": "",
                "win_explanation": "",
                "also_covered": [],
            }
        ]
        app.session_state["owner_authenticated"] = True
        app.session_state[f"generated_briefing_{edition_key}"] = {
            "window_start": raw_feed["window_start"],
            "window_end": raw_feed["window_end"],
            "executive_summary": "The Army completed a new aviation test.",
            "what_to_watch": [],
            "sections": sections,
            "usage": {},
            "model": "test",
        }
        app.run()

        win_key = f"edit_{edition_key}_military-story_is_win"
        self.assertIn(win_key, {item.key for item in app.checkbox if item.key})

        app.session_state[win_key] = True
        app.run()

        self.assertIn(
            f"edit_{edition_key}_military-story_eo_number",
            {item.key for item in app.selectbox if item.key},
        )
        self.assertIn(
            f"edit_{edition_key}_military-story_eo_section",
            {item.key for item in app.text_input if item.key},
        )


if __name__ == "__main__":
    unittest.main()
