from __future__ import annotations

from datetime import date
import unittest

from regulatory_tracker import build_regulatory_tracker
from streamlit.testing.v1 import AppTest


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

    def test_tracker_is_visible_and_selectable_before_build(self) -> None:
        app = self.load_app()
        tracker = build_regulatory_tracker(date(2026, 7, 15))

        self.assertIn(
            "Regulatory Deadline Tracker",
            [item.value for item in app.subheader],
        )
        checkbox_keys = {item.key for item in app.checkbox if item.key}
        for item in tracker:
            self.assertIn(
                f"build_2026-07-15_tracker_{item['id']}_include",
                checkbox_keys,
            )

    def test_legacy_briefing_gets_tracker_before_what_to_watch(self) -> None:
        app = self.load_app()
        tracker = build_regulatory_tracker(date(2026, 7, 15))
        sections = {
            section: []
            for section in (
                "Trump Administration Wins",
                "Top Developments",
                "UAS and Drones",
                "UAS Security and C-UAS",
                "eVTOL Integration Pilot Program and AAM",
                "Autonomous Vehicles",
                "Other Advanced Transportation",
                "Federal Actions",
            )
        }
        app.session_state["owner_authenticated"] = True
        app.session_state[
            "build_2026-07-15_tracker_bvlos-part-108_include"
        ] = False
        app.session_state["generated_briefing_2026-07-15"] = {
            "window_start": "2026-07-14T04:15:00-04:00",
            "window_end": "2026-07-15T04:15:00-04:00",
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
                f"edit_2026-07-15_tracker_{item['id']}_include",
                checkbox_keys,
            )
        self.assertFalse(
            app.session_state[
                "edit_2026-07-15_tracker_bvlos-part-108_include"
            ]
        )
        subheaders = [item.value for item in app.subheader]
        self.assertLess(
            subheaders.index("Regulatory Deadline Tracker"),
            subheaders.index("What to Watch"),
        )


if __name__ == "__main__":
    unittest.main()
