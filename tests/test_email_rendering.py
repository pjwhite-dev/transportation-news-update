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
    def test_military_section_is_in_required_order(self) -> None:
        order = streamlit_app.SECTION_ORDER
        self.assertLess(order.index("UAS Security and C-UAS"), order.index("Military"))
        self.assertLess(
            order.index("Military"),
            order.index("eVTOL Integration Pilot Program and AAM"),
        )
        self.assertLess(
            order.index("Other Advanced Transportation"),
            order.index("International"),
        )
        self.assertLess(order.index("International"), order.index("Federal Actions"))

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

    def test_headlines_at_a_glance_follow_summary_and_mirror_sections(self) -> None:
        current = briefing()
        av_title = "California approves Waymo robotaxi expansion"
        international_title = "India launches its first hydrogen train"
        current["sections"]["Top Developments"] = [
            {
                "id": "av-1",
                "title": av_title,
                "summary": "The deployment will add driverless service.",
                "source": "Example News",
                "url": "https://example.com/av",
                "date_label": "Jul. 15, 2026",
                "published": "2026-07-15T03:00:00-04:00",
                "section": "Autonomous Vehicles",
                "is_administration_win": False,
                "also_covered": [],
            }
        ]
        current["sections"]["International"] = [
            {
                "id": "international-1",
                "title": international_title,
                "summary": "The train uses hydrogen fuel cells.",
                "source": "Example News",
                "url": "https://example.com/international",
                "date_label": "Jul. 15, 2026",
                "published": "2026-07-15T02:00:00-04:00",
                "section": "International",
                "is_administration_win": False,
                "also_covered": [],
            }
        ]

        groups = dict(streamlit_app.sectioned_headline_groups(current))
        self.assertEqual(
            [item["title"] for item in groups["Top Developments"]],
            [av_title],
        )
        self.assertEqual(
            [item["title"] for item in groups["International"]],
            [international_title],
        )

        web = streamlit_app.build_web_preview_html(current)
        outlook = streamlit_app.build_outlook_html(current)
        plain = streamlit_app.build_plain_text(current)
        for name, rendered in (("web", web), ("outlook", outlook), ("plain", plain)):
            with self.subTest(format=name):
                lowered = rendered.casefold()
                self.assertLess(
                    lowered.index("executive summary"),
                    lowered.index("headlines at a glance"),
                )
                self.assertIn(av_title, rendered)
                self.assertIn(international_title, rendered)
        self.assertIn("font-size:12px", web)
        self.assertIn("font-size:11px", outlook)

    def test_legacy_session_story_is_recategorized_as_international(self) -> None:
        current = briefing()
        story = {
            "id": "madrid-av",
            "title": "Madrid approves a new commercial robotaxi service",
            "summary": "Spanish regulators cleared the driverless deployment.",
            "source": "Example News",
            "url": "https://example.com/madrid-av",
            "date_label": "Jul. 15, 2026",
            "published": "2026-07-15T02:00:00-04:00",
            "section": "Autonomous Vehicles",
            "importance": 5,
            "is_administration_win": False,
            "also_covered": [],
        }
        current["sections"]["Autonomous Vehicles"] = [story]

        streamlit_app.ensure_current_story_sections(current)

        self.assertEqual(current["sections"]["Autonomous Vehicles"], [])
        self.assertEqual(current["sections"]["International"], [story])

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
