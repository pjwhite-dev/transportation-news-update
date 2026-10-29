from __future__ import annotations

from datetime import date
from html import unescape
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
    def test_duplicate_description_is_not_rendered(self) -> None:
        current = briefing()
        title = "Army fields a new autonomous reconnaissance drone"
        current["sections"]["Military"] = [
            {
                "id": "military-1",
                "title": title,
                "summary": title + ".",
                "source": "U.S. Army",
                "url": "https://www.army.mil/example",
                "date_label": "Jul. 15, 2026",
                "section": "Military",
                "is_administration_win": False,
                "also_covered": [],
            }
        ]

        for name, rendered in (
            ("web", streamlit_app.build_web_preview_html(current)),
            ("outlook", streamlit_app.build_outlook_html(current)),
            ("plain", streamlit_app.build_plain_text(current)),
        ):
            with self.subTest(format=name):
                self.assertNotIn(title + ".", rendered)

    def test_top_highlights_are_omitted_without_matching_items(self) -> None:
        current = briefing()
        current["regulatory_tracker"] = []

        for rendered in (
            streamlit_app.build_web_preview_html(current),
            streamlit_app.build_outlook_html(current),
            streamlit_app.build_plain_text(current),
        ):
            self.assertNotIn("IMMINENT REGULATORY DEADLINES", rendered)
            self.assertNotIn("INNOVATIVE UAS USES", rendered)

    def test_outlook_title_heading_sizes_and_footer_match_requested_copy(self) -> None:
        current = briefing()
        current["sections"]["UAS Security and C-UAS"] = [
            {
                "id": "cuas-1",
                "title": "Airport tests a new counter-UAS sensor",
                "summary": "The trial evaluates drone detection technology.",
                "source": "Example News",
                "url": "https://example.com/cuas",
                "date_label": "Jul. 15, 2026",
                "section": "UAS Security and C-UAS",
                "is_administration_win": False,
                "also_covered": [],
            }
        ]

        outlook = streamlit_app.build_outlook_html(current)
        web = streamlit_app.build_web_preview_html(current)
        plain = streamlit_app.build_plain_text(current)

        self.assertIn("Advanced Transportation Update", outlook)
        self.assertIn("font-size:26pt", outlook)
        self.assertIn("font-size:18pt", outlook)
        self.assertNotIn("font-size:18px", outlook)
        for rendered in (web, outlook, plain):
            self.assertIn("Public source, AI-assisted news update.", rendered)
            self.assertNotIn("Review summaries, links", rendered)
        self.assertTrue(
            plain.endswith("Public source, AI-assisted news update.")
        )

    def test_eo_display_includes_plain_english_section_summary(self) -> None:
        citation = streamlit_app.eo_display(
            {
                "eo_number": "EO 14307",
                "eo_section": "Section 3",
            }
        )

        self.assertEqual(
            citation,
            "Unleashing American Drone Dominance (EO 14307, Section 3, "
            "advancing domestic commercialization of UAS technologies at scale)",
        )

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
                self.assertIn("Closed Feb. 11, 2026", rendered)
                self.assertIn("comments closed Feb. 11, 2026", rendered)
                self.assertNotIn("pending final rule", rendered)  # status comes from the FR record, not a guess

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
