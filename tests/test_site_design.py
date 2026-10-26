from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path

import public_site


def edition(day: str, stories: dict[str, list[dict]], watch=None) -> dict:
    return {
        "briefing_date": day,
        "window_start": f"{day}T00:00:00-04:00",
        "window_end": f"{day}T04:30:00-04:00",
        "executive_summary": "NHTSA proposed new lighting rules. The FAA cleared a flight.",
        "sections": stories,
        "what_to_watch": watch if watch is not None else ["Nov. 25 — Comments due on the NHTSA lighting proposal."],
        "regulatory_tracker": [
            {
                "id": "x", "agency": "NHTSA", "rin": "2127-AM70", "action": "FMVSS No. 108 lighting",
                "comment_deadline": "2026-11-25", "comment_deadline_label": "November 25, 2026",
                "days_remaining": 30, "status": "Open for comment",
                "source_url": "https://www.federalregister.gov/d/2026-21794",
                "verified_at": "2026-10-26T04:40:00-04:00",
            },
            {
                "id": "y", "agency": "FAA", "rin": "2120-AL82", "action": "Part 108",
                "comment_deadline": "2026-02-11", "comment_deadline_label": "February 11, 2026",
                "days_remaining": None, "status": "Pending final rule",
                "source_url": "https://www.federalregister.gov/d/2026-02649",
            },
        ],
    }


def story(title: str, source: str, importance: int = 5, summary: str = "A concrete summary of the event.") -> dict:
    return {
        "id": title, "title": title, "summary": summary, "source": source, "importance": importance,
        "url": f"https://example.com/{title.lower().replace(' ', '-')}", "date_label": "Oct. 25, 2026",
    }


class SiteDesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "repo"
        archive = root / "data" / "archive"
        archive.mkdir(parents=True)
        first = edition("2026-10-23", {"Autonomous Vehicles": [story("Waymo expands to Phoenix airport", "Reuters")]})
        second = edition(
            "2026-10-26",
            {
                "Autonomous Vehicles": [
                    story("NHTSA proposes cyan lights for robotaxis - Electrek", "electrek.co", importance=9),
                    story("Zoox recalls fleet", "Reuters", importance=6),
                ],
                "Counter-UAS / Airspace Security": [story("Army buys long-range interceptor", "Defense News", importance=4)],
            },
        )
        (archive / "2026-10-23.json").write_text(json.dumps(first), encoding="utf-8")
        (archive / "2026-10-26.json").write_text(json.dumps(second), encoding="utf-8")
        cls.out = Path(cls.tmp.name) / "site"
        cls.result = public_site.build_public_site(root, cls.out)
        cls.index = (cls.out / "index.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_site_is_named_update_everywhere(self) -> None:
        self.assertEqual(public_site.SITE_TITLE, "Advanced Transportation Update")
        self.assertIn("<title>Advanced Transportation Update — Monday, October 26, 2026</title>", self.index)
        self.assertIn("Advanced Transportation <i>Update</i>", self.index)
        self.assertNotIn("Transportation Daily", self.index)
        self.assertNotIn("Transportation Daily", (self.out / "email.html").read_text(encoding="utf-8"))

    def test_every_edition_page_carries_an_exact_edition_id_marker(self) -> None:
        self.assertIn('<meta name="edition-id" content="2026-10-26">', self.index)
        old = (self.out / "archive" / "2026-10-23" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<meta name="edition-id" content="2026-10-23">', old)
        self.assertNotIn('content="2026-10-26"', old)
        self.assertNotIn("edition-id", (self.out / "search" / "index.html").read_text(encoding="utf-8"))

    def test_hero_leads_with_the_top_story_without_the_publisher_tag(self) -> None:
        hero = re.search(r'<div class="wrap hero">.*?</h1>', self.index, re.S).group(0)
        self.assertIn("NHTSA proposes cyan lights for robotaxis</a>", hero)
        self.assertNotIn("Electrek", hero)

    def test_phone_gets_a_collapsible_menu_instead_of_a_scroll_row(self) -> None:
        self.assertIn('<details class="menu"><summary>Jump to a section</summary>', self.index)
        css = (self.out / "assets" / "site.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"(?s)@media \(max-width:760px\)\{.*\.chips\{display:none\}.*\.menu\{display:block\}")
        self.assertEqual(self.index.count('href="#autonomous-vehicles"'), 2)  # chips + menu

    def test_docket_is_collapsed_at_the_foot_and_summarised_in_one_line(self) -> None:
        match = re.search(r'<details class="docket" id="tracker">.*?</details>', self.index, re.S)
        self.assertIsNotNone(match)
        docket = match.group(0)
        self.assertNotIn(" open", docket.split(">", 1)[0])
        self.assertIn("2 rulemakings tracked", docket)
        self.assertIn("Next date: November 25, 2026", docket)
        self.assertGreater(self.index.index('id="tracker"'), self.index.rindex('class="sector"'))
        self.assertNotIn("Regulatory Tracker", self.index)

    def test_what_to_watch_lives_in_the_sidebar_with_its_date_split_out(self) -> None:
        self.assertRegex(self.index, r'<aside class="side no-copy".*<time>Nov\. 25</time><span>Comments due')

    def test_copy_for_email_still_wired_to_the_edition_content(self) -> None:
        self.assertIn("data-copy-edition", self.index)
        self.assertIn("data-edition-content", self.index)
        self.assertIn("data-outlook-email-b64", self.index)

    def test_search_index_has_headlines_only_and_working_links(self) -> None:
        rows = json.loads((self.out / "search-index.json").read_text(encoding="utf-8"))
        self.assertEqual(self.result["search_rows"], len(rows))
        self.assertEqual(len(rows), 4)
        self.assertEqual({"d", "t", "s", "h"} - set(rows[0]), set())
        self.assertTrue(all(set(row) <= {"d", "t", "s", "o", "h"} for row in rows))
        blob = json.dumps(rows)
        self.assertNotIn("A concrete summary", blob)  # summaries and article text are not indexed
        self.assertNotIn("Electrek", blob)
        for row in rows:
            day, anchor = re.fullmatch(r"/archive/(\d{4}-\d\d-\d\d)/#(story-[0-9a-f]{12})", row["h"]).groups()
            page = (self.out / "archive" / day / "index.html").read_text(encoding="utf-8")
            self.assertIn(f'id="{anchor}"', page)

    def test_search_and_archive_pages_exist_and_link_up(self) -> None:
        search = (self.out / "search" / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-index="../search-index.json"', search)
        self.assertIn("not the text of the articles", search)
        archive = (self.out / "archive" / "index.html").read_text(encoding="utf-8")
        self.assertIn("<h2>October 2026</h2>", archive)
        self.assertIn("NHTSA proposes cyan lights for robotaxis", archive)
        self.assertIn('href="../search/"', archive)
        for page in (self.index, search, archive):
            self.assertIn("Search</a>", page)

    def test_summary_renders_paragraphs_and_superscript_source_citations(self) -> None:
        payload = {
            "executive_summary": "First claim [1](https://example.com/a-story). Second claim [2](https://example.com/b-story).\n\nNew paragraph [1](https://example.com/a-story).",
            "sections": {"Autonomous Vehicles": [{"url": "https://example.com/a-story"}, {"url": "https://example.com/b-story"}]},
        }
        paragraphs = public_site.summary_paragraphs(payload)
        self.assertEqual(len(paragraphs), 2)
        self.assertIn('claim<sup class="cite"><a href="https://example.com/a-story"', paragraphs[0])
        self.assertNotIn("](", paragraphs[0])
        payload["executive_summary"] += " Bad [3](https://evil.example/x)."
        self.assertNotIn("evil.example", "".join(public_site.summary_paragraphs(payload)))

    def test_display_title_strips_only_a_matching_publisher_suffix(self) -> None:
        clean = public_site.display_title
        self.assertEqual(clean({"title": "Zoox recalls fleet - WION", "source": "wionews.com"}), "Zoox recalls fleet")
        self.assertEqual(clean({"title": "A - B - C story", "source": "Reuters"}), "A - B - C story")
        self.assertEqual(clean({"title": "Great story | Fortune", "source": "Fortune"}), "Great story")

    def test_plural_helper_and_lead_selection(self) -> None:
        self.assertEqual(public_site._plural(1, "story"), "1 story")
        self.assertEqual(public_site._plural(30, "story"), "30 stories")
        self.assertEqual(public_site._plural(2, "rulemaking"), "2 rulemakings")
        self.assertIsNone(public_site.lead_story({"sections": {}}))
        self.assertEqual(public_site.lead_story(edition("2026-10-26", {"Autonomous Vehicles": [story("Low", "x", 1), story("High", "x", 8)]}))["title"], "High")


if __name__ == "__main__":
    unittest.main()
