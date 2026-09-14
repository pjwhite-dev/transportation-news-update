from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from public_site import (
    SITE_CSS,
    build_public_site,
    edition_page,
    headline_index_html,
    outlook_email_html,
    prepare_automated_edition,
    story_html,
)
import news_engine


def raw_feed(day: str, articles: list[dict]) -> dict:
    return {
        "generated_at": f"{day}T04:16:00-04:00",
        "window_start": f"{day}T04:15:00-04:00",
        "window_end": f"{day}T04:15:00-04:00",
        "articles": articles,
        "candidate_count": len(articles),
    }


def article(title: str, url: str, section: str = "UAS and Drones") -> dict:
    return {
        "id": title,
        "search_section": section,
        "title": title,
        "summary": "",
        "source": "Example News",
        "url": url,
        "published": "2026-09-10T03:00:00-04:00",
        "date_label": "Sep. 10, 2026",
        "origin": "Google News RSS",
    }


class PublicSiteTests(unittest.TestCase):
    def test_pages_workflow_redeploys_for_renderer_dependencies(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "publish-news-site.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('- "news_engine.py"', workflow)

    def test_headlines_at_a_glance_includes_every_story_and_links_into_edition(self) -> None:
        sections = {
            "Top Developments": [
                article(f"Development {number}", f"https://example.com/top-{number}")
                for number in range(1, 10)
            ],
            "UAS and Drones": [
                article(f"Drone story {number}", f"https://example.com/uas-{number}")
                for number in range(1, 10)
            ],
        }

        rendered = headline_index_html(sections)
        outlook = outlook_email_html(
            {"executive_summary": "Summary.", "sections": sections},
            date(2026, 9, 14),
        )

        self.assertIn("Top Developments", rendered)
        self.assertIn("UAS and Drones", rendered)
        self.assertEqual(rendered.count('href="#story-'), 18)
        self.assertNotIn('href="https://example.com/', rendered)
        self.assertEqual(outlook.count("Development 9"), 2)
        self.assertEqual(outlook.count("Drone story 9"), 2)

    def test_edition_uses_unified_masthead_and_section_navigation(self) -> None:
        payload = {
            "window_end": "2026-09-11T04:15:00-04:00",
            "executive_summary": "A concise executive summary.",
            "edition_kind": "editorial",
            "sections": {
                "UAS and Drones": [
                    article("FAA advances drone integration", "https://example.com/drone")
                ]
            },
            "regulatory_tracker": [],
            "what_to_watch": [],
        }

        rendered = edition_page(payload, date(2026, 9, 11), archived=False)

        self.assertIn('<span>Advanced Transportation</span><small>News Update</small>', rendered)
        self.assertIn('<h1>Friday, September 11, 2026</h1>', rendered)
        self.assertIn('class="section-nav no-copy"', rendered)
        self.assertIn('href="#uas-drones"', rendered)
        self.assertIn('href="#wins"', rendered)
        self.assertIn("No qualifying Administration implementation developments", rendered)
        self.assertIn('id="summary"', rendered)
        self.assertIn("position:sticky", SITE_CSS)

    def test_story_renderer_canonicalizes_source_and_uses_quiet_uas_label(self) -> None:
        rendered = story_html(
            {
                "title": "A new drone operation",
                "summary": "The operator began a new inspection service.",
                "source": "dronelife",
                "url": "https://example.com/story",
                "innovative_uas_use": "Inspecting remote infrastructure.",
            }
        )

        self.assertIn(">DroneLife<", rendered)
        self.assertIn('class="innovation-label"', rendered)
        self.assertNotIn('class="highlight"', rendered)

    def test_outlook_renderer_is_self_contained_and_table_based(self) -> None:
        payload = {
            "executive_summary": "A concise executive summary.",
            "sections": {
                "UAS and Drones": [
                    article(
                        "FAA advances drone integration",
                        "https://example.com/drone",
                    )
                ]
            },
            "regulatory_tracker": [],
            "what_to_watch": ["Watch the next FAA filing."],
        }

        rendered = outlook_email_html(payload, date(2026, 9, 11))

        self.assertIn('role="presentation"', rendered)
        self.assertIn('style="color:#173C5E;text-decoration:underline"', rendered)
        self.assertIn("Advanced Transportation News Update", rendered)
        self.assertNotIn('class="', rendered)

    def test_story_renderer_removes_internal_editorial_commentary(self) -> None:
        rendered = story_html(
            {
                "title": "Army tests a new unmanned aircraft",
                "summary": (
                    "The service completed a flight demonstration. "
                    "The supplied record does not show a procurement milestone."
                ),
                "source": "Example News",
                "url": "https://example.com/story",
            }
        )

        self.assertIn("completed a flight demonstration", rendered)
        self.assertNotIn("supplied record", rendered)

    def test_raw_publication_filter_rejects_stock_and_non_ads_fmvss_items(self) -> None:
        stock = article(
            "Tesla vs. Waymo: Which robotaxi stock wins? (TSLA)",
            "https://example.com/stock",
            "Autonomous Vehicles",
        )
        stock["source"] = "Seeking Alpha"
        generic_fmvss = article(
            "NHTSA updates FMVSS 213 for child restraint systems",
            "https://example.com/fmvss",
            "Federal Actions",
        )
        generic_fmvss["origin"] = "Federal Register API"

        self.assertFalse(news_engine.automated_record_is_publication_worthy(stock))
        self.assertFalse(
            news_engine.automated_record_is_publication_worthy(generic_fmvss)
        )

    def test_iranian_underwater_drone_capture_is_military(self) -> None:
        item = article(
            "Iran captures an underwater drone near a naval vessel",
            "https://example.com/underwater-drone",
        )

        self.assertEqual(news_engine.infer_section(item), "Military")

    def test_same_event_is_consolidated_across_sections(self) -> None:
        first = article(
            "Pony AI starts driverless Robotaxi tests in Zagreb",
            "https://example.com/first",
            "Autonomous Vehicles",
        )
        second = article(
            "Pony AI begins driverless Robotaxi tests in Zagreb, Croatia",
            "https://example.com/second",
            "International",
        )
        second["source"] = "International News"

        edition = prepare_automated_edition(
            raw_feed("2026-09-10", [first, second]),
            [],
        )

        self.assertEqual(edition["sections"]["Autonomous Vehicles"], [])
        self.assertEqual(len(edition["sections"]["International"]), 1)
        self.assertEqual(
            edition["sections"]["International"][0]["also_covered"][0]["url"],
            second["url"],
        )

    def test_publisher_location_does_not_make_us_story_international(self) -> None:
        item = article(
            "Retailer plans delivery drone service in Tacoma",
            "https://example.com/tacoma",
        )
        item["source"] = "Yahoo News Canada"

        self.assertEqual(news_engine.infer_section(item), "UAS and Drones")

    def test_generic_fmcsa_exemption_is_not_publication_worthy(self) -> None:
        item = article(
            "Qualification of Drivers; Epilepsy Exemption Applications",
            "https://example.com/fmcsa",
            "Federal Actions",
        )
        item.update(
            origin="Federal Register API",
            source="Federal Motor Carrier Safety Administration",
            summary="FMCSA renews medical exemptions for five drivers.",
        )

        self.assertFalse(news_engine.automated_record_is_publication_worthy(item))

    def test_latest_page_stays_on_complete_edition_until_next_build(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_archive = root / "data" / "raw_archive"
            final_archive = root / "data" / "archive"
            raw_archive.mkdir(parents=True)
            final_archive.mkdir(parents=True)

            prior = {
                "window_start": "2026-09-08T04:15:00-04:00",
                "window_end": "2026-09-09T04:15:00-04:00",
                "executive_summary": "A prior editorial summary.",
                "sections": {
                    "UAS and Drones": [
                        article(
                            "FAA proposes Part 108 BVLOS drone operations rule",
                            "https://example.com/earlier",
                        )
                    ]
                },
                "regulatory_tracker": [],
                "what_to_watch": [],
            }
            (final_archive / "2026-09-09.json").write_text(json.dumps(prior))
            latest = raw_feed(
                "2026-09-10",
                [
                    article(
                        "FAA Part 108 BVLOS drone operations rule draws industry comments",
                        "https://example.com/repeat",
                    ),
                    article(
                        "City begins autonomous shuttle operations downtown",
                        "https://example.com/fresh",
                        "Autonomous Vehicles",
                    ),
                ],
            )
            (raw_archive / "2026-09-10.json").write_text(json.dumps(latest))
            output = root / "site"

            result = build_public_site(root, output)

            latest_html = (output / "index.html").read_text()
            archive_html = (output / "archive" / "index.html").read_text()
            old_html = (
                output / "archive" / "2026-09-09" / "index.html"
            ).read_text()
            raw_html = (
                output / "archive" / "2026-09-10" / "index.html"
            ).read_text()
            cname = (output / "CNAME").read_text()

        self.assertEqual(result["edition_count"], 2)
        self.assertIn("Advanced Transportation News Update", latest_html)
        self.assertIn("Copy for email", latest_html)
        self.assertIn("data-outlook-email-b64", latest_html)
        self.assertIn("A prior editorial summary.", latest_html)
        self.assertNotIn("City begins autonomous shuttle operations", latest_html)
        self.assertIn("2026-09-09/", archive_html)
        self.assertIn("2026-09-10/", archive_html)
        self.assertIn("A prior editorial summary.", old_html)
        self.assertIn("City begins autonomous shuttle operations", raw_html)
        self.assertNotIn("draws industry comments", raw_html)
        self.assertEqual(cname, "news.peterjwhite.org\n")


if __name__ == "__main__":
    unittest.main()
