from __future__ import annotations

from datetime import date
import unittest

from regulatory_tracker import build_regulatory_tracker
import streamlit_app


def briefing() -> dict:
    sections = {
        section: []
        for section in streamlit_app.SECTION_ORDER
    }
    return {
        "window_start": "2026-07-14T04:15:00-04:00",
        "window_end": "2026-07-15T04:15:00-04:00",
        "executive_summary": "A concise summary.",
        "sections": sections,
        "regulatory_tracker": build_regulatory_tracker(date(2026, 7, 15)),
        "what_to_watch": ["Watch the next agency action."],
    }


class RegulatoryTrackerRenderingTests(unittest.TestCase):
    def test_tracker_precedes_what_to_watch_in_all_full_formats(self) -> None:
        current = briefing()
        for name, rendered in (
            ("web", streamlit_app.build_web_preview_html(current)),
            ("outlook", streamlit_app.build_outlook_html(current)),
            ("plain", streamlit_app.build_plain_text(current)),
        ):
            with self.subTest(format=name):
                self.assertIn("Regulatory Deadline Tracker", rendered.title())
                self.assertLess(
                    rendered.casefold().index("regulatory deadline tracker"),
                    rendered.casefold().index("what to watch"),
                )
                self.assertIn("Closed February 11, 2026", rendered)
                self.assertIn("Pending final rule", rendered)

    def test_tracker_is_omitted_from_executive_versions(self) -> None:
        current = briefing()
        self.assertNotIn(
            "Regulatory Deadline Tracker",
            streamlit_app.build_outlook_html(current, executive_only=True),
        )
        self.assertNotIn(
            "REGULATORY DEADLINE TRACKER",
            streamlit_app.build_plain_text(current, executive_only=True),
        )

    def test_outlook_has_no_count_based_at_a_glance_strip(self) -> None:
        rendered = streamlit_app.build_outlook_html(briefing())

        self.assertNotIn("TODAY AT A GLANCE", rendered)
        self.assertNotIn("total items", rendered)


if __name__ == "__main__":
    unittest.main()
