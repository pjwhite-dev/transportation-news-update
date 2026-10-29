"""Regression tests for the second round of Oct 29 fixes, built from that day's real headlines."""

from __future__ import annotations

import unittest
from datetime import date

import public_site
from article_extract import official_links
from qa_report import QAReport
from quality_pass import QualityContext, attach_official_sources, cap_edition, topic_is_incidental
from story_groups import group_within_edition

RAIL = "Advanced Rail / High-Speed Rail"


def story(title: str, section: str, url: str, summary: str = "", **extra) -> dict:
    return {"title": title, "section": section, "url": url, "summary": summary, "source": "Example", "importance": 6, **extra}


def context(**kwargs) -> QualityContext:
    return QualityContext(report=QAReport("2026-10-29"), today=date(2026, 10, 29), **kwargs)


class SameEventTests(unittest.TestCase):
    SPAIN = [
        story("Spain mourns after latest train tragedy kills 68", RAIL, "https://www.yahoo.com/a",
              "Spain declared three days of mourning after a Renfe train struck an Iryo train in Castilla-La Mancha.", source="Yahoo"),
        story("Dozens dead in Spain after high-speed train slams into another stopped on tracks", RAIL, "https://www.pbs.org/b",
              "A Renfe high-speed train hit a stopped Iryo train in Castilla-La Mancha, Spain.", source="PBS"),
        story("Europe Today: Second high-speed rail disaster in nine months", RAIL, "https://www.euronews.com/c",
              "Spain is mourning after a collision between Renfe and Iryo trains in Castilla-La Mancha.", source="Euronews"),
        story("Colorado Connector Offers Alternative Route to Passenger Rail", RAIL, "https://example.com/d",
              "Colorado planners proposed a connector for Front Range passenger rail."),
    ]

    def test_one_crash_worded_three_ways_is_one_item_with_additional_coverage(self) -> None:
        asked = []

        def judge(a, b):
            asked.append((a["title"], b["title"]))
            spain = ("Spain" in a["summary"]) and ("Spain" in b["summary"])
            return spain, "same collision" if spain else "different events"

        merged, log = group_within_edition([dict(s) for s in self.SPAIN], judge=judge)
        self.assertEqual(len(merged), 2)
        crash = merged[0]
        self.assertEqual(len(crash["also_covered"]), 2)
        self.assertIn("verifier", log[0]["reason"])
        # Colorado shares no name with the Spain stories, so it is never put to the verifier.
        self.assertFalse([pair for pair in asked if "Colorado" in pair[0] + pair[1]])

    def test_additional_coverage_lists_each_outlet_once_and_at_most_six(self) -> None:
        from story_groups import MAX_ALSO_COVERED, merge_group

        lead = story("Spain mourns after latest train tragedy kills 68", RAIL, "https://www.yahoo.com/a")
        lead["also_covered"] = [{"source": f"Outlet {i}", "url": f"https://o{i}.com/x"} for i in range(9)]
        twin = dict(story("Dozens dead in Spain", RAIL, "https://o3.com/other"), source="Outlet 3")
        merged = merge_group([lead, twin])
        outlets = [e["source"] for e in merged["also_covered"]]
        self.assertEqual(len(outlets), MAX_ALSO_COVERED)
        self.assertEqual(len(outlets), len(set(outlets)))

    def test_without_a_judge_or_across_sections_nothing_new_is_merged(self) -> None:
        merged, _ = group_within_edition([dict(s) for s in self.SPAIN])
        self.assertEqual(len(merged), 4)
        other = [dict(self.SPAIN[0]), dict(self.SPAIN[1], section="International Security")]
        merged, _ = group_within_edition(other, judge=lambda a, b: (True, "x"))
        self.assertEqual(len(merged), 2)

    def test_a_verifier_error_leaves_stories_apart(self) -> None:
        def broken(a, b):
            raise RuntimeError("ollama down")

        merged, _ = group_within_edition([dict(s) for s in self.SPAIN], judge=broken)
        self.assertEqual(len(merged), 4)


class CasualtyUpdateTests(unittest.TestCase):
    YESTERDAY = {"edition": "2026-10-28", "title": "At Least 30 Dead As Two High-Speed Rail Trains Collide in Spain",
                 "summary": "A high-speed train slammed into another, killing at least 30 people near Urda."}

    def test_a_higher_death_toll_is_an_update_and_the_same_toll_is_a_repeat(self) -> None:
        from story_groups import new_facts_since

        today = story("Spain mourns after latest train tragedy kills 68", RAIL, "https://www.yahoo.com/a",
                      "Spain declared three days of mourning after the collision killed 68 people.")
        self.assertIn("68", new_facts_since(today, self.YESTERDAY))
        same = story("Thirty dead in Spain train collision", RAIL, "https://e.com/b", "The crash killed at least 30 people.")
        self.assertEqual(new_facts_since(same, self.YESTERDAY), "")


class VerifierBudgetTests(unittest.TestCase):
    def test_yes_no_checks_leave_the_verifier_room_to_answer(self) -> None:
        # 200 tokens let gemma run out before writing JSON; every check then failed silently (2026-10-29 replay).
        from types import SimpleNamespace

        from quality_pass import make_topic_check
        from story_groups import make_same_event_check

        budgets = []

        class Verifier:
            def generate_structured(self, messages, schema_name, schema, max_output_tokens):
                budgets.append(max_output_tokens)
                return SimpleNamespace(value={"same_event": True, "on_topic": True, "reason": "x"})

        make_same_event_check(Verifier())(SameEventTests.SPAIN[0], SameEventTests.SPAIN[1])
        make_topic_check(Verifier())(SameEventTests.SPAIN[0], RAIL)
        self.assertTrue(all(b >= 800 for b in budgets), budgets)


class TopicTests(unittest.TestCase):
    def test_incidental_drone_mention_is_dropped_on_topic_opinion_is_kept(self) -> None:
        mines = story("3 oil tankers catch fire after striking naval mines in Strait of Hormuz", "Military UAS",
                      "https://example.com/mines", "Three tankers hit mines; officials said drones were not involved.")
        sabotage = story("Russia's alleged sabotage campaign in Europe raises fears", "International Security",
                         "https://example.com/sabotage", "Officials cited arson, cable cuts and drone sightings over bases.")
        opinion = story("What Symbotic's Walmart growth teaches robotics startups", "Robotics", "https://example.com/op",
                        "An analysis of Symbotic's warehouse robot deployments.")
        asked = []

        def on_topic(item, section):
            asked.append(item["title"])
            return ("sabotage" in item["title"], "drone incursions are a large part of the campaign")

        ctx = context(on_topic=on_topic)
        kept = topic_is_incidental([mines, sabotage, opinion], ctx)
        self.assertEqual([s["url"] for s in kept], ["https://example.com/sabotage", "https://example.com/op"])
        self.assertNotIn(opinion["title"], asked)  # "robotics" in the headline: not incidental, not asked
        self.assertTrue(any("incidental" in str(e) for e in ctx.report.events))

    def test_required_items_and_missing_verifier_keep_everything(self) -> None:
        mines = story("3 oil tankers catch fire after striking naval mines", "Military UAS", "https://example.com/m",
                      required_include=True)
        self.assertEqual(len(topic_is_incidental([mines], context(on_topic=lambda s, sec: (False, "no")))), 1)
        self.assertEqual(len(topic_is_incidental([dict(mines, required_include=False)], context())), 1)


class CapTests(unittest.TestCase):
    def test_past_the_cap_the_lowest_priority_go_and_each_section_keeps_its_best(self) -> None:
        stories = [story(f"AV story {i}", "Autonomous Vehicles", f"https://e.com/av{i}", priority_tier=2, importance=9 - i % 5)
                   for i in range(24)]
        stories += [story(f"Military story {i}", "Military UAS", f"https://e.com/m{i}", priority_tier=3, importance=8)
                    for i in range(5)]
        stories += [story("FRA awards $2.3 billion", "Federal Policy & Implementation", "https://e.com/fra", priority_tier=1, importance=4)]
        report = QAReport("2026-10-29")
        kept = cap_edition(stories, report, cap=25)
        self.assertEqual(len(kept), 25)
        sections = {s["section"] for s in kept}
        self.assertEqual(sections, {"Autonomous Vehicles", "Military UAS", "Federal Policy & Implementation"})
        self.assertEqual(sum(1 for s in kept if s["section"] == "Military UAS"), 1)
        self.assertEqual(len([e for e in report.events if "edition-cap" in str(e)]), 5)
        self.assertEqual([s["url"] for s in kept], [s["url"] for s in stories if s in kept])  # edition order kept

    def test_under_the_cap_nothing_changes(self) -> None:
        stories = [story(f"s{i}", "Robotics", f"https://e.com/{i}") for i in range(10)]
        self.assertEqual(cap_edition(stories, QAReport("2026-10-29")), stories)


class OfficialSourceTests(unittest.TestCase):
    PAGE = """<nav><a href="https://www.usa.gov/">USA.gov</a><a href="https://www.whitehouse.gov/privacy/">Privacy</a></nav>
    <p>The President signed <a href="https://www.whitehouse.gov/presidential-actions/2026/10/adjusting-imports-of-robotics/">a proclamation</a>
    after a <a href="https://www.commerce.gov/news/press-releases/2026/10/section-232-robotics">Commerce finding</a>.
    <a href="https://www.whitehouse.gov/">Home</a> <a href="https://example.com/other">other</a></p>"""

    def test_article_links_to_government_pages_are_collected_without_boilerplate(self) -> None:
        self.assertEqual(official_links(self.PAGE), [
            "https://www.whitehouse.gov/presidential-actions/2026/10/adjusting-imports-of-robotics/",
            "https://www.commerce.gov/news/press-releases/2026/10/section-232-robotics",
        ])

    def proclamation(self) -> dict:
        return story("Trump Directs Robotics Import Talks After Commerce Security Finding", "Federal Policy & Implementation",
                     "https://www.unite.ai/trump-directs-robotics-import-talks/", "President Trump issued a proclamation.",
                     priority_tier=1, page_official_links=official_links(self.PAGE),
                     also_covered=[{"source": "Reuters", "url": "https://www.reuters.com/x"}])

    def test_a_verified_government_page_is_linked_first_and_marked_official(self) -> None:
        pages = {"https://www.whitehouse.gov/presidential-actions/2026/10/adjusting-imports-of-robotics/":
                 {"title": "Adjusting Imports of Robotics and Industrial Machinery", "text": "By the President of the United States..."}}
        item = self.proclamation()
        ctx = context(fetch_official=pages.get, same_event=lambda a, b: (True, "the proclamation itself"))
        attach_official_sources([item], ctx)
        first = item["also_covered"][0]
        self.assertEqual((first["source"], first["official"]), ("The White House", True))
        self.assertEqual(item["also_covered"][1]["source"], "Reuters")
        html = public_site.story_html(item)
        self.assertIn("<strong>Official source:</strong>", html)
        self.assertIn("whitehouse.gov/presidential-actions", html)
        self.assertLess(html.index("Official source"), html.index("Additional coverage"))

    def test_nothing_is_linked_unless_the_page_is_live_and_the_verifier_agrees(self) -> None:
        item = self.proclamation()
        attach_official_sources([item], context(fetch_official=lambda u: None, same_event=lambda a, b: (True, "")))
        self.assertEqual(len(item["also_covered"]), 1)
        attach_official_sources([item], context(fetch_official=lambda u: {"title": "t", "text": "x"},
                                                same_event=lambda a, b: (False, "different action")))
        self.assertEqual(len(item["also_covered"]), 1)

    def test_government_and_non_federal_stories_are_not_searched(self) -> None:
        calls = []
        fetch = lambda u: calls.append(u)  # noqa: E731
        gov = dict(self.proclamation(), url="https://www.transportation.gov/briefing-room/x")
        industry = dict(self.proclamation(), priority_tier=2, title="Audi plans humanoid robot trial to sort factory parts")
        attach_official_sources([gov, industry], context(fetch_official=fetch, same_event=lambda a, b: (True, "")))
        self.assertEqual(calls, [])

    def test_a_government_record_in_todays_pool_is_a_candidate(self) -> None:
        item = story("FRA Announces $2.3B for Passenger, Freight Rail Projects Nationwide", RAIL, "https://www.metro-magazine.com/fra",
                     "The FRA is investing $2.3 billion through the CRISI program.", priority_tier=2)  # "announces": tier 2, still searched
        record = {"title": "FRA Awards $2.3 Billion in CRISI Grants", "url": "https://railroads.dot.gov/newsroom/press-releases/crisi-2026",
                  "summary": "The FRA announced $2.3 billion in CRISI grants."}
        ctx = context(fetch_official=lambda u: {"title": record["title"], "text": record["summary"]},
                      same_event=lambda a, b: (True, "same grant round"), official_records=[record])
        attach_official_sources([item], ctx)
        self.assertEqual(item["also_covered"][0]["source"], "FRA")

    def test_the_agency_site_is_searched_and_a_blocked_page_is_judged_on_the_snippet(self) -> None:
        item = story("FRA Announces $2.3B for Passenger, Freight Rail Projects Nationwide", RAIL, "https://www.metro-magazine.com/fra",
                     "The FRA is investing $2.3 billion through the CRISI program.", priority_tier=2)
        release = "https://railroads.dot.gov/about-fra/communications/newsroom/press-releases/golden-age-rail-2"
        queries, judged = [], []

        def search(headline, site):
            queries.append(site)
            return [{"url": "https://www.example.com/not-gov", "title": "x", "text": "x"},
                    {"url": release, "title": "Duffy Invests Over $2.3 Billion into Rail", "text": "FRA announced $2.3 billion in CRISI grants."}]

        def same_event(a, b):
            judged.append(b["title"])
            return True, "same grant round"

        ctx = context(fetch_official=lambda u: None, same_event=same_event, search_official=search)  # DOT answers 403
        attach_official_sources([item], ctx)
        self.assertEqual(queries, ["railroads.dot.gov"])
        self.assertEqual(judged, ["Duffy Invests Over $2.3 Billion into Rail"])
        self.assertEqual((item["also_covered"][0]["url"], item["also_covered"][0]["official"]), (release, True))

    def test_a_search_that_finds_nothing_the_verifier_accepts_links_nothing(self) -> None:
        item = self.proclamation()
        ctx = context(fetch_official=lambda u: None, same_event=lambda a, b: (False, "an older proclamation"),
                      search_official=lambda h, s: [{"url": "https://www.whitehouse.gov/presidential-actions/2025/x/", "title": "t", "text": "x"}])
        attach_official_sources([item], ctx)
        self.assertEqual(len(item["also_covered"]), 1)

    def test_search_results_are_government_articles_from_the_past_week(self) -> None:
        from unittest.mock import Mock

        from article_extract import make_official_search

        session = Mock()
        session.get.return_value.json.return_value = {"results": [
            {"url": "https://railroads.dot.gov/newsroom/press-releases", "title": "Press Releases", "content": ""},
            {"url": "https://railroads.dot.gov/about-fra/communications/newsroom/press-releases/golden-age-rail-2", "title": "t", "content": "c"},
            {"url": "https://www.railwayage.com/fra-grants", "title": "t", "content": "c"},
        ]}
        results = make_official_search("http://localhost:8080", session)("FRA Announces $2.3B", "railroads.dot.gov")
        self.assertEqual([r["url"] for r in results], ["https://railroads.dot.gov/about-fra/communications/newsroom/press-releases/golden-age-rail-2"])
        params = session.get.call_args.kwargs["params"]
        self.assertEqual(params["time_range"], "week")
        self.assertTrue(params["q"].endswith("site:railroads.dot.gov"))


if __name__ == "__main__":
    unittest.main()
