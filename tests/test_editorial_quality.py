"""Regression tests for the Oct. 27 quality failures. Fixtures are real saved pages unless the file name
ends in _reconstructed (congress.gov and AP answer automated requests with 403, so those were rebuilt from
the facts the owner reported)."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import article_extract as ax
import calendar_rules as cal
import editorial_checks as checks
import grounding
import market_watch
import public_site
import story_groups as groups
import tracker.core as tracker
from qa_report import QAReport

ET = ZoneInfo("America/New_York")
FIX = Path(__file__).resolve().parent / "fixtures" / "editorial"
ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 27, 9, 33, tzinfo=ET)


def page(name: str, url: str = "https://example.com/"):
    return 200, (FIX / name).read_text(encoding="utf-8"), url


def evaluate(record, name, **kwargs):
    return ax.evaluate_candidate(record, page(name, record["url"]), now=NOW, **kwargs)


class StaleStoryTests(unittest.TestCase):
    def test_1_daily_star_black_sea_story_from_oct_6_is_dropped_as_stale(self):
        record = {"title": "Attack sinks Turkish ship in Black Sea | The Daily Star", "source": "thedailystar.net",
                  "url": "https://www.thedailystar.net/news/world/news/attack-sinks-turkish-ship-black-sea-4291506"}
        verdict = evaluate(record, "dailystar_black_sea.html")
        self.assertFalse(verdict.keep)
        self.assertIn("stale", verdict.reason)
        self.assertIn("2026-10-06", verdict.reason)

    def test_2_house_report_from_june_15_is_dropped_as_stale(self):
        record = {"title": "H. Rept. 119-698 - NATIONAL DEFENSE AUTHORIZATION ACT FOR FISCAL YEAR 2027", "source": "congress.gov",
                  "url": "https://www.congress.gov/committee-report/119th-congress/house-report/698/1"}
        verdict = evaluate(record, "congress_hrept_698_reconstructed.html")
        self.assertFalse(verdict.keep)
        self.assertIn("stale", verdict.reason)
        self.assertIn("2026-06-15", verdict.reason)

    def test_an_item_with_no_findable_date_is_dropped_not_labelled(self):
        verdict = ax.evaluate_candidate({"title": "A long enough headline here", "url": "https://example.com/story"},
                                        (200, "<html><body><article><p>" + "word " * 200 + "</p></article></body></html>", ""), now=NOW)
        self.assertFalse(verdict.keep)
        self.assertIn("no verifiable publication date", verdict.reason)

    def test_a_fresh_article_keeps_its_real_date_not_the_crawl_date(self):
        verdict = evaluate({"title": "Ukraine war briefing: Russia strikes Kyiv hours before truce", "source": "AOL",
                            "url": "https://www.aol.co.uk/articles/ukraine-war-briefing-russia-strikes-013027000.html",
                            "published": "2026-10-27T04:00:00-04:00"}, "aol_ukraine_briefing.html")
        self.assertTrue(verdict.keep, verdict.reason)
        self.assertEqual(verdict.record["date_source"], "json-ld datePublished")
        self.assertTrue(verdict.record["published"].startswith("2026-10-26"))
        self.assertEqual(verdict.record["date_label"], "Oct. 26, 2026")

    def test_related_story_widgets_do_not_date_an_article(self):
        html = ("<html><body><article><h1>FAA names new drone corridor</h1><span>Published: 6 October 2026</span>"
                + "<p>" + "The agency announced the corridor today. " * 40 + "</p></article>"
                "<aside><time datetime='2026-10-27T01:00:00+00:00'>x</time><time datetime='2026-10-25T01:00:00+00:00'>y</time></aside></body></html>")
        self.assertEqual(ax.extract_page(html, "https://example.com/a").published.date(), date(2026, 10, 6))

    def test_window_is_48_hours_and_72_over_weekends_and_holidays(self):
        self.assertEqual(cal.freshness_hours(datetime(2026, 10, 27, 9, tzinfo=ET)), 48)   # Tuesday
        self.assertEqual(cal.freshness_hours(datetime(2026, 10, 26, 9, tzinfo=ET)), 72)   # Monday
        self.assertEqual(cal.freshness_hours(datetime(2026, 10, 13, 9, tzinfo=ET)), 72)   # Tuesday after Columbus Day
        self.assertEqual(cal.freshness_hours(datetime(2026, 10, 24, 9, tzinfo=ET)), 72)   # Saturday


class NotArticleTests(unittest.TestCase):
    def test_3_stock_quote_page_is_dropped(self):
        record = {"title": "JOBY AVIATION News and Stock Quote (NYSE: JOBY ... - Green Stock News", "source": "greenstocknews.com",
                  "url": "https://www.greenstocknews.com/market-data/stock-quote/joby-aviation"}
        verdict = evaluate(record, "greenstocknews_joby.html")
        self.assertFalse(verdict.keep)
        self.assertIn("not an article", verdict.reason)

    def test_4_ap_hub_and_ain_section_pages_are_dropped(self):
        hub = ax.evaluate_candidate({"title": "Russia-Ukraine | Breaking News & Live Updates | AP News", "source": "apnews.com",
                                     "url": "https://apnews.com/hub/russia-ukraine"}, None, now=NOW)
        self.assertFalse(hub.keep)
        self.assertIn("not an article", hub.reason)
        section = evaluate({"title": "Defense | Aviation International News", "source": "ainonline.com",
                            "url": "https://www.ainonline.com/section/defense"}, "ain_section_defense.html")
        self.assertFalse(section.keep)

    def test_5_railway_gazette_author_page_is_resolved_or_dropped_and_publisher_name_never_published(self):
        record = {"title": "Railway Gazette International", "source": "railwaygazette.com",
                  "url": "https://www.railwaygazette.com/author/railway-gazette-international/"}
        verdict = evaluate(record, "railwaygazette_author.html")
        if verdict.keep:
            self.assertNotEqual(verdict.record["title"].casefold(), "railway gazette international")
            self.assertNotIn("/author/", verdict.record["url"])
        else:
            self.assertIn("not an article", verdict.reason)

    def test_a_listing_page_resolves_to_the_story_it_links_when_the_headline_matches(self):
        listing = ('<html><body><a href="/author/jane">Jane Doe</a>'
                   '<a href="/news/siemens-wins-california-high-speed-rail-trainset-order">Siemens wins California high-speed rail trainset order</a>'
                   "</body></html>")
        target = ax.resolve_listing_to_article(listing, "https://www.example.com/author/jane/", "Siemens wins California high-speed rail trainset order")
        self.assertEqual(target, "https://www.example.com/news/siemens-wins-california-high-speed-rail-trainset-order")

    def test_short_pages_are_not_articles(self):
        html = ('<html><head><meta property="article:published_time" content="2026-10-27T01:00:00-04:00"></head>'
                "<body><h1>A short note about drones today</h1><p>Only a few words here today ok.</p></body></html>")
        verdict = ax.evaluate_candidate({"title": "A short note about drones today", "url": "https://example.com/a-short-note"}, (200, html, ""), now=NOW)
        self.assertFalse(verdict.keep)
        self.assertIn("words of body text", verdict.reason)


class HeadlineTests(unittest.TestCase):
    def test_broken_headlines_are_all_rejected(self):
        bad = {
            "Railway Gazette International": ["Railway Gazette International"],
            "Ukraine war briefing Russia strikes 013027000.html": [],
            "JOBY AVIATION News and Stock Quote (NYSE: JOBY ...": [],
            "Company announces plan meeting indigenous eV": [],
            "Breaking News from the Senate": [],
            "Live Updates: the latest on drones": [],
        }
        for headline, names in bad.items():
            self.assertTrue(ax.headline_problems(headline, publication_names=names), headline)

    def test_clean_headlines_pass_and_site_suffixes_are_stripped(self):
        self.assertEqual(ax.headline_problems("FAA proposes new rules for drone corridors"), [])
        self.assertEqual(ax.strip_site_suffix("FAA proposes drone corridor rule | Aviation Week", "Aviation Week"), "FAA proposes drone corridor rule")
        self.assertEqual(ax.strip_site_suffix("FAA proposes drone corridor rule - Reuters", "Reuters"), "FAA proposes drone corridor rule")

    def test_6_aol_page_gets_its_real_headline_not_the_url_slug(self):
        verdict = evaluate({"title": "Ukraine war briefing Russia strikes 013027000.html", "source": "AOL",
                            "url": "https://www.aol.co.uk/articles/ukraine-war-briefing-russia-strikes-013027000.html"},
                           "aol_ukraine_briefing.html")
        if verdict.keep:
            self.assertNotIn("013027000", verdict.record["title"])
            self.assertTrue(verdict.record["title"].startswith("Ukraine war briefing: Russia strikes Kyiv"))
        else:
            self.assertIn("headline", verdict.reason)

    def test_non_english_headlines_are_translated_or_the_item_is_dropped(self):
        record = {"title": "Les drones de la police survolent la ville pour la premiere fois", "source": "Le Monde",
                  "url": "https://example.com/fr/story"}
        html = ('<html><head><meta property="article:published_time" content="2026-10-27T01:00:00-04:00">'
                '<meta property="og:title" content="Les drones de la police survolent la ville pour la premiere fois"></head><body><article><p>'
                + "Les drones de la police ont survole la ville hier soir avec une autorisation speciale. " * 20 + "</p></article></body></html>")
        no_translator = ax.evaluate_candidate(record, (200, html, ""), now=NOW)
        self.assertFalse(no_translator.keep)
        translated = ax.evaluate_candidate(record, (200, html, ""), now=NOW, translate=lambda text: "Police drones fly over the city for the first time")
        self.assertTrue(translated.keep, translated.reason)
        self.assertEqual(translated.record["title"], "Police drones fly over the city for the first time")


class GroundingTests(unittest.TestCase):
    SOURCE = (FIX / "fr_2026-21794_abstract.txt").read_text(encoding="utf-8")

    def test_7_requiring_cyan_lights_fails_grounding_and_regeneration_says_optional(self):
        bad = "NHTSA proposed requiring cyan marker lamps on automated driving system vehicles."
        problems = grounding.deterministic_problems(bad, self.SOURCE)
        self.assertTrue(any(p.kind == "modal" for p in problems), problems)
        good = "NHTSA proposed to allow cyan-colored ADS marker lamps as optional lighting equipment in ADS-equipped vehicles."
        calls = []

        def write(title, source, why):
            calls.append(why)
            return good

        summary, record = grounding.ground_summary("FMVSS No. 108", self.SOURCE, write=write,
                                                   entail=lambda src, sents: [(True, "")] * len(sents), draft=bad)
        self.assertEqual(record.method, "regenerated")
        self.assertIn("optional", summary)
        self.assertNotIn("requir", summary.lower())
        self.assertTrue(record.failures and "modal" in record.failures[0])
        self.assertEqual(len(calls), 1)

    def test_8_black_sea_summary_with_300_russian_linked_vessels_fails_grounding(self):
        html = (FIX / "dailystar_black_sea.html").read_text(encoding="utf-8")
        body = ax.extract_page(html, "https://www.thedailystar.net/x").body_text
        problems = grounding.deterministic_problems(
            "Ukrainian forces have struck over 300 Russian-linked vessels in recent weeks.", body)
        self.assertTrue(any(p.kind == "number" and "300" in p.detail for p in problems), problems)
        grounded = grounding.deterministic_problems("Russian drones attacked a grain ship in the Black Sea, Zelensky said.", body)
        self.assertEqual([p for p in grounded if p.kind in ("number", "modal", "descriptor")], [])

    def test_9_pedrosa_summary_with_motogp_champion_fails_grounding(self):
        html = (FIX / "pedrosa_cybercab_reconstructed.html").read_text(encoding="utf-8")
        body = ax.extract_page(html, "https://example.com/p").body_text
        problems = grounding.deterministic_problems("Former MotoGP champion Dani Pedrosa rode Tesla's Cybercab in Austin.", body)
        kinds = {p.kind for p in problems}
        self.assertIn("descriptor", kinds)
        self.assertTrue(any("champion" in p.detail for p in problems))
        self.assertEqual([p for p in grounding.deterministic_problems("Rider Dani Pedrosa took a ride in Tesla's driverless Cybercab in Austin.", body)
                          if p.kind == "descriptor"], [])

    def test_unsupported_sentence_triggers_one_regeneration_then_the_extractive_fallback(self):
        source = ("Regulators approved the permit on Monday after a review of the application that lasted several months. "
                  "The permit covers 12 aircraft at the airport and runs for five years. "
                  "Officials said work begins next month.")
        attempts = []

        def write(title, src, why):
            attempts.append(why)
            return "Regulators approved the permit on Monday after a review."

        summary, record = grounding.ground_summary("Permit", source, write=write,
                                                   entail=lambda s, sents: [(False, "not stated")] * len(sents), draft="")
        self.assertEqual(record.method, "extractive-fallback")
        self.assertTrue(summary.startswith("Regulators approved the permit on Monday after a review of the application"))
        self.assertEqual(len(attempts), 2)  # the first draft and exactly one strict regeneration
        self.assertEqual(len(record.failures), 2)

    def test_filler_phrases_are_blocked_unless_the_source_says_them(self):
        filler = ["This development is part of the broader trend toward automation.", "The move marks a significant step for the industry.",
                  "The incident has prompted an investigation.", "This move suggests a shift."]
        for sentence in filler:
            self.assertTrue(grounding.filler_problems(sentence), sentence)
        self.assertEqual(grounding.filler_problems("The incident has prompted an investigation.", "Officials said the incident has prompted an investigation."), [])
        self.assertEqual(grounding.drop_filler_sentences("Regulators approved 12 permits. This move suggests a shift."), "Regulators approved 12 permits.")


class GroupAndRepeatTests(unittest.TestCase):
    ARCHIVE = ROOT / "data" / "archive"

    def cyan_stories(self, day: str):
        briefing = json.loads((self.ARCHIVE / f"{day}.json").read_text(encoding="utf-8"))
        return [{**s, "section": sec} for sec, items in briefing["sections"].items() for s in items
                if re.search(r"cyan|fmvss no\. 108", s["title"] + s.get("summary", ""), re.I)]

    def test_10_cyan_story_is_a_full_item_only_on_the_first_day(self):
        results = {}
        for day in ("2026-10-25", "2026-10-26", "2026-10-27"):
            as_of = date.fromisoformat(day)
            store = groups.build_store(self.ARCHIVE, as_of)
            kept, log = groups.drop_repeats(self.cyan_stories(day), store, as_of)
            results[day] = (len(kept), len(log))
        self.assertGreater(results["2026-10-25"][0], 0)
        self.assertEqual(results["2026-10-25"][1], 0)
        self.assertEqual(results["2026-10-26"][0], 0)
        self.assertEqual(results["2026-10-27"][0], 0)

    def test_one_event_covered_by_several_outlets_becomes_one_item_with_additional_coverage(self):
        stories = self.cyan_stories("2026-10-25")
        merged, log = groups.group_within_edition(stories)
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(log), len(stories) - 1)
        self.assertTrue(merged[0]["also_covered"])

    def test_federal_register_document_and_press_story_are_one_rulemaking(self):
        fr = {"title": "Federal Motor Vehicle Safety Standards; Modernization of FMVSS No. 108", "url": "https://www.federalregister.gov/documents/2026/10/26/2026-21794/x",
              "summary": "", "source": "NHTSA", "importance": 5, "also_covered": []}
        press = {"title": "NHTSA proposes cyan lights for robotaxis", "url": "https://example.com/cyan", "summary": "", "source": "Example",
                 "importance": 4, "also_covered": []}
        merged, _ = groups.group_within_edition([press, fr])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["url"], fr["url"])  # the official record leads

    def test_a_new_development_is_allowed_through(self):
        store = [{"edition": "2026-10-20", "title": "FAA proposes Part 108 drone rule", "url": "https://a.example/1", "urls": ["https://a.example/1"],
                  "identifiers": ["rin:2120-AL82"], "tokens": ["faa", "proposes", "part", "108", "drone", "rule"], "numbers": [],
                  "summary": "The FAA proposed the rule.", "led": False, "section": ""}]
        update = {"title": "FAA finalizes Part 108 drone rule", "summary": "The agency finalized the Part 108 rule, covering 5,000 drones.",
                  "url": "https://b.example/2", "section": "UAS / Drones"}
        repeat = {"title": "FAA Part 108 drone rule explained", "summary": "The FAA proposed the rule.", "url": "https://c.example/3",
                  "section": "UAS / Drones"}
        kept, log = groups.drop_repeats([update, repeat], store, date(2026, 10, 27))
        self.assertEqual([s["url"] for s in kept], ["https://b.example/2"])
        self.assertIn("update_of", kept[0])
        self.assertEqual(len(log), 1)

    def test_lead_eligibility_blocks_a_story_that_led_a_recent_edition(self):
        briefing = json.loads((self.ARCHIVE / "2026-10-26.json").read_text(encoding="utf-8"))
        self.assertTrue(groups.lead_urls(briefing))


class DocketTests(unittest.TestCase):
    ITEMS = {i["key"]: i for i in tracker.build_tracker(date(2026, 10, 27))}

    def test_11_docket_status_comes_from_federal_register_data(self):
        fmcsa = self.ITEMS["2126-AC17"]
        self.assertTrue(fmcsa["status"].startswith("Pre-proposal (SANPRM, Feb. 1, 2023)"), fmcsa["status"])
        self.assertNotIn("pending", fmcsa["status"].lower())
        self.assertEqual(fmcsa["citation"], "88 FR 6691")
        part555 = self.ITEMS["2127-AM57"]
        self.assertTrue(part555["status"].startswith("Interim final rule in effect July 31, 2026"), part555["status"])
        self.assertIn("comments closed", part555["status"])
        part108 = self.ITEMS["2120-AL82"]
        self.assertEqual(part108["oira_status"], "Under OIRA review since July 10, 2026")
        self.assertIn("2026-10-29", [d["date"] for d in part108["upcoming"] if d["kind"] == "meeting"])
        fcc = self.ITEMS["fcc-space-modernization"]
        self.assertIn("SB Docket No. 25-306", fcc["docket"])
        self.assertEqual(fcc["citation"], "91 FR 67225")
        self.assertEqual({d["date"] for d in fcc["upcoming"]}, {"2026-11-25", "2026-12-28"})

    def test_next_date_counts_days_only_for_real_deadlines(self):
        fcc = self.ITEMS["fcc-space-modernization"]
        self.assertEqual(fcc["next_date"]["days_remaining"], 29)
        part108 = self.ITEMS["2120-AL82"]
        self.assertEqual(part108["next_date"]["kind"], "meeting")
        self.assertIsNone(part108["next_date"]["days_remaining"])

    def test_document_classification_uses_type_and_action_not_prose(self):
        def doc(kind, action):
            return {"type": kind, "action": action}
        self.assertEqual(tracker.classify_document(doc("Proposed Rule", "Advance notice of proposed rulemaking (ANPRM).")), "anprm")
        self.assertEqual(tracker.classify_document(doc("Proposed Rule", "Supplemental advance notice of proposed rulemaking (SANPRM).")), "anprm")
        self.assertEqual(tracker.classify_document(doc("Proposed Rule", "Notice of proposed rulemaking (NPRM); Reopening of comment period.")), "extension")
        self.assertEqual(tracker.classify_document(doc("Rule", "Interim final rule; request for comments.")), "ifr")
        self.assertEqual(tracker.classify_document(doc("Rule", "Final rule.")), "final")

    def test_a_comment_extension_updates_the_close_date(self):
        events = [
            {"rin": "R", "document_number": "1", "type": "Proposed Rule", "action": "Notice of proposed rulemaking (NPRM).",
             "publication_date": "2026-09-01", "comments_close_on": "2026-10-01", "effective_on": "", "citation": "91 FR 1", "docket_ids": []},
            {"rin": "R", "document_number": "2", "type": "Proposed Rule", "action": "Extension of comment period.",
             "publication_date": "2026-09-25", "comments_close_on": "2026-11-15", "effective_on": "", "citation": "91 FR 2", "docket_ids": []},
        ]
        derived = tracker.derive_status(events, date(2026, 10, 27))
        self.assertEqual(derived["comment_deadline"], "2026-11-15")
        self.assertEqual(derived["status"], "Proposed rule (Sept. 1, 2026); comments close Nov. 15, 2026")

    def test_final_rule_status_names_publication_and_effective_dates(self):
        events = [{"rin": "R", "document_number": "9", "type": "Rule", "action": "Final rule.", "publication_date": "2026-10-01",
                   "comments_close_on": "", "effective_on": "2026-12-01", "citation": "91 FR 9", "docket_ids": []}]
        derived = tracker.derive_status(events, date(2026, 10, 27))
        self.assertEqual(derived["status"], "Final rule published Oct. 1, 2026; effective Dec. 1, 2026")
        self.assertEqual(derived["deadlines"][0]["date"], "2026-12-01")

    def test_oira_review_file_is_parsed_for_tracked_rins_only(self):
        xml = ("<OIRA_DATA><REGACT><RIN>2120-AL82</RIN><STAGE>Final Rule</STAGE><DATE_RECEIVED>2026-07-10</DATE_RECEIVED></REGACT>"
               "<REGACT><RIN>9999-ZZ99</RIN><STAGE>Proposed Rule</STAGE><DATE_RECEIVED>2026-01-01</DATE_RECEIVED></REGACT></OIRA_DATA>")
        found = tracker.parse_oira_xml(xml, {"2120-AL82"})
        self.assertEqual(found, {"2120-AL82": {"under_review": True, "received": "2026-07-10", "stage": "Final Rule"}})

    def test_daily_discovery_adds_new_matching_rulemakings_and_skips_known_ones(self):
        class Response:
            def __init__(self, results):
                self._results = results

            def raise_for_status(self):
                return None

            def json(self):
                return {"results": self._results}

        doc = {"title": "Operation of Unmanned Aircraft Systems Over Open-Air Assemblies", "abstract": "Drone flights over crowds.",
               "regulation_id_numbers": ["2120-AZ99"], "docket_ids": ["FAA-2026-9999"], "agencies": [{"short_name": "FAA", "name": "Federal Aviation Administration"}]}
        noise = {"title": "Polar bear take", "abstract": "mentions an unmanned aircraft once", "regulation_id_numbers": ["1018-BI93"], "docket_ids": ["X"], "agencies": []}

        class Session:
            def get(self, *args, **kwargs):
                return Response([doc, noise])

        found = tracker.discover(Session(), NOW, set(), set())
        self.assertEqual([e["rin"] for e in found], ["2120-AZ99"])
        self.assertEqual(found[0]["agency"], "FAA")
        self.assertEqual(tracker.discover(Session(), NOW, {"2120-AZ99"}, set()), [])


class SectionScopeTests(unittest.TestCase):
    def story(self, title, summary="", url="https://example.com/a-story-here", **extra):
        return {"title": title, "summary": summary, "url": url, "importance": 3, **extra}

    def test_12_rail_items_move_to_advanced_rail_and_the_navy_collision_is_dropped(self):
        sections = {
            "UAS / Drones": [
                self.story("Siemens Mobility to build trainsets for California High-Speed Rail"),
                self.story("$43.6MM CRISI Grant Awarded to Southeastern Short Line Improvement Project"),
                self.story("FTA Advances $164M Broward County Commuter Rail Project"),
            ],
            "Military UAS": [self.story("2 US Navy ships collide in Caribbean, minor injuries reported")],
            "Advanced Rail / High-Speed Rail": [],
        }
        scoped, log = checks.enforce_section_scope(sections)
        self.assertEqual(len(scoped["Advanced Rail / High-Speed Rail"]), 3)
        self.assertEqual(scoped["UAS / Drones"], [])
        self.assertEqual(scoped["Military UAS"], [])
        self.assertTrue(any("Navy" in entry["title"] and "dropped" in entry["reason"] for entry in log))

    def test_off_topic_and_wrong_section_items_from_oct_27_are_handled(self):
        sections = {
            "Military UAS": [self.story("SpaceX Starship delivery test completes first launch from new pad"),
                             self.story("General Ukraine war update: front lines unchanged")],
            "International Security": [self.story("India's first eVTOL air taxi completes test flight"),
                                       self.story("AUKUS crewed submarine delivery slips again")],
            "Autonomous Vehicles": [self.story("Dani Pedrosa rides Tesla's driverless Cybercab in Austin")],
            "AAM / eVTOL / Advanced Aviation": [], "Other Advanced Transportation": [],
        }
        scoped, log = checks.enforce_section_scope(sections)
        self.assertEqual([s["title"] for s in scoped["AAM / eVTOL / Advanced Aviation"]], ["India's first eVTOL air taxi completes test flight"])
        self.assertEqual(scoped["International Security"], [])
        self.assertEqual(scoped["Autonomous Vehicles"], [])
        self.assertEqual(scoped["Military UAS"], [])
        reasons = " ".join(entry["reason"] for entry in log)
        self.assertIn("celebrity or human-interest", reasons)
        self.assertIn("fits no section scope", reasons + " fits no section scope")

    def test_a_drone_story_stays_in_military_uas_and_scope_is_idempotent(self):
        sections = {"Military UAS": [self.story("Army tests interceptor drones against loitering munitions")], "UAS / Drones": []}
        once, _ = checks.enforce_section_scope(sections)
        twice, log = checks.enforce_section_scope(once)
        self.assertEqual(once, twice)
        self.assertEqual(log, [])
        self.assertEqual(len(once["Military UAS"]), 1)

    def test_every_section_has_a_definition_and_required_terms(self):
        from editorial_config import section_scopes
        from news_engine import SECTION_ORDER

        scopes = section_scopes()
        for section in SECTION_ORDER:
            self.assertIn(section, scopes, section)
            self.assertTrue(scopes[section]["definition"])
            self.assertIsNotNone(scopes[section]["require"])


class MarketLabelTests(unittest.TestCase):
    def snapshot(self, quote_time):
        quotes = [{"symbol": s, "name": n, "group": g, "price": 10.0, "change": 1.0, "percent": 1.0, "quote_time": quote_time}
                  for s, n, g in market_watch.WATCHLIST]
        return {"as_of": "2026-10-27T13:00:00+00:00", "source": "Finnhub", "quotes": quotes}

    def test_13_label_at_930_am_on_oct_27_is_the_oct_26_close(self):
        snapshot = self.snapshot("2026-10-26T20:00:00+00:00")
        label = market_watch.snapshot_label(snapshot, datetime(2026, 10, 27, 9, 30, tzinfo=ET))
        self.assertEqual(label, "Close, Mon., Oct. 26")
        markup = public_site.market_html({**snapshot, "session_label": label})
        self.assertIn("Close, Mon., Oct. 26.", markup)
        self.assertNotIn("Prior-day", markup)

    def test_data_from_another_session_shows_its_own_date_and_no_timestamps_hides_the_panel(self):
        stale = market_watch.snapshot_label(self.snapshot("2026-10-23T20:00:00+00:00"), datetime(2026, 10, 27, 9, 30, tzinfo=ET))
        self.assertEqual(stale, "Close, Fri., Oct. 23")
        intraday = market_watch.snapshot_label(self.snapshot("2026-10-27T15:00:00+00:00"), datetime(2026, 10, 27, 11, 30, tzinfo=ET))
        self.assertEqual(intraday, "Intraday, Tue., Oct. 27")
        bare = {"as_of": "2026-10-27T13:00:00+00:00", "quotes": [{"symbol": "SPY", "name": "S&P", "group": "Market overview", "price": 1.0, "percent": 1.0}] * 8}
        self.assertEqual(market_watch.snapshot_label(bare), "")
        self.assertEqual(public_site.market_html(bare), "")

    def test_nyse_calendar(self):
        self.assertTrue(cal.is_nyse_session(date(2026, 10, 26)))
        self.assertFalse(cal.is_nyse_session(date(2026, 11, 26)))   # Thanksgiving
        self.assertFalse(cal.is_nyse_session(date(2026, 4, 3)))     # Good Friday
        self.assertFalse(cal.is_nyse_session(date(2026, 7, 3)))     # Independence Day observed
        self.assertEqual(cal.last_completed_nyse_session(datetime(2026, 11, 27, 9, 0, tzinfo=ET)), date(2026, 11, 25))
        self.assertEqual(cal.last_completed_nyse_session(datetime(2026, 10, 26, 17, 0, tzinfo=ET)), date(2026, 10, 26))
        self.assertEqual(cal.last_completed_nyse_session(datetime(2026, 10, 26, 10, 0, tzinfo=ET)), date(2026, 10, 23))


class SourceLabelTests(unittest.TestCase):
    def test_14_aap_label(self):
        self.assertEqual(checks.source_label("https://aapnews.aap.com.au/"), "AAP")
        self.assertEqual(public_site.canonical_source("https://aapnews.aap.com.au/"), "AAP")

    def test_clean_english_names_and_original_publisher_for_syndicated_copies(self):
        self.assertEqual(checks.source_label("https://www.chosun.com/x"), "Chosun Ilbo")
        self.assertEqual(checks.source_label("asiae.co.kr"), "Asia Business Daily")
        self.assertEqual(checks.source_label("AOL", "https://www.aol.co.uk/a.html", "theguardian.com"), "Theguardian")
        self.assertEqual(checks.source_label("Yahoo Finance", "https://finance.yahoo.com/n", "Reuters"), "Reuters")
        self.assertEqual(checks.source_label("Defense News"), "Defense News")

    def test_retail_and_quote_hosts_are_denied(self):
        for url in ("https://www.basenor.com/products/drone-kit", "https://www.greenstocknews.com/market-data/stock-quote/x"):
            self.assertIn("denied host", ax.url_rejection(url))


class StyleTests(unittest.TestCase):
    def test_money_is_written_out(self):
        self.assertEqual(checks.style_money("A $43.6MM grant and $1.2B and $500K."), "A $43.6 million grant and $1.2 billion and $500 thousand.")

    def test_known_executive_orders_are_named_by_title_in_italics_with_a_footnote(self):
        text = checks.style_executive_orders("Executive Order 14307 directs the FAA; EO 14305 followed. Executive Order 99999 is unknown.")
        self.assertIn("*Unleashing American Drone Dominance*", text)
        self.assertIn("*Restoring American Airspace Sovereignty*", text)
        self.assertIn("Executive Order 99999", text)   # a title is never guessed
        rendered = public_site.rich(text)
        self.assertIn("<em>Unleashing American Drone Dominance</em>", rendered)
        self.assertIn("Executive Order 14307", public_site.eo_note(text))

    def test_no_exclamation_points_in_tags(self):
        self.assertEqual(checks.strip_exclamations("Innovative Drone Use!"), "Innovative Drone Use")


class QAReportTests(unittest.TestCase):
    def test_report_lists_every_drop_and_writes_json_and_markdown(self):
        report = QAReport("2026-10-27")
        report.add("article-check", "Attack sinks Turkish ship", "stale: published 2026-10-06", "https://example.com/a")
        report.add("summary-regenerated", "FMVSS 108", "modal: summary says 'requiring'", "https://example.com/b")
        with tempfile.TemporaryDirectory() as tmp:
            json_path, md_path = report.write(Path(tmp))
            data = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(data["summary"], {"article-check": 1, "summary-regenerated": 1})
            self.assertIn("stale: published 2026-10-06", md_path.read_text(encoding="utf-8"))
            self.assertEqual(QAReport.from_dict(data).events, report.events)


class EnrichStageTests(unittest.TestCase):
    def test_stage_keeps_good_articles_drops_bad_ones_saves_html_and_reports_reasons(self):
        pages = {
            "https://www.aol.co.uk/articles/ukraine-war-briefing-russia-strikes-013027000.html": page("aol_ukraine_briefing.html"),
            "https://www.thedailystar.net/news/world/news/attack-sinks-turkish-ship-black-sea-4291506": page("dailystar_black_sea.html"),
            "https://www.greenstocknews.com/market-data/stock-quote/joby-aviation": page("greenstocknews_joby.html"),
        }
        articles = [{"id": str(i), "title": "t", "source": "s", "url": url} for i, url in enumerate(pages)]
        articles.append({"id": "hub", "title": "Russia-Ukraine | Breaking News", "source": "AP", "url": "https://apnews.com/hub/russia-ukraine"})
        with tempfile.TemporaryDirectory() as tmp:
            kept, dropped = ax.enrich_candidates(articles, now=NOW, fetched_dir=Path(tmp) / "fetched", fetch=lambda url: pages.get(url, (None, "", url)))
        self.assertEqual([a["url"] for a in kept], ["https://www.aol.co.uk/articles/ukraine-war-briefing-russia-strikes-013027000.html"])
        self.assertEqual(len(dropped), 3)
        self.assertTrue(all(d["reason"] for d in dropped))
        self.assertEqual(kept[0]["date_source"], "json-ld datePublished")
        self.assertGreater(len(kept[0]["page_text"]), 500)

    def test_required_editor_items_are_never_dropped(self):
        article = {"id": "x", "title": "Short", "source": "s", "url": "https://apnews.com/hub/x", "required_include": True}
        kept, dropped = ax.enrich_candidates([article], now=NOW, fetch=lambda url: (None, "", url))
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, [])


class QualityPassTests(unittest.TestCase):
    BODY = ("The Federal Aviation Administration said on Monday that it approved 12 drone corridors in Texas. "
            "The corridors let operators fly beyond visual line of sight below 400 feet. "
            "Officials said the first flights begin next month.")

    def story(self, title, url, section, **extra):
        return {"id": url, "title": title, "summary": "feed text", "url": url, "section": section, "importance": 5, "source": "https://aapnews.aap.com.au/",
                "published": "2026-10-27T05:00:00-04:00", "date_label": "Oct. 27, 2026", "also_covered": [], "page_text": self.BODY, **extra}

    def run_pass(self, sections, store=None):
        from qa_report import QAReport
        from quality_pass import QualityContext, quality_pass

        context = QualityContext(
            report=QAReport("2026-10-27"), today=date(2026, 10, 27), store=store or [],
            write=lambda title, source, why: "The FAA approved 12 drone corridors in Texas that let operators fly beyond visual line of sight below 400 feet.",
            entail=lambda source, sentences: [(True, "")] * len(sentences),
        )
        return quality_pass(sections, context), context

    def test_pass_labels_sources_regrounds_summaries_and_drops_off_scope_items(self):
        sections = {
            "UAS / Drones": [self.story("FAA approves 12 drone corridors in Texas", "https://example.com/faa-corridors", "UAS / Drones"),
                             self.story("Siemens wins California High-Speed Rail trainset order", "https://example.com/siemens-rail", "UAS / Drones")],
            "Military UAS": [self.story("2 US Navy ships collide in Caribbean", "https://example.com/navy", "Military UAS")],
            "Advanced Rail / High-Speed Rail": [],
        }
        result, context = self.run_pass(sections)
        faa = result["UAS / Drones"][0]
        self.assertEqual(faa["source"], "AAP")
        self.assertTrue(faa["summary"].startswith("The FAA approved 12 drone corridors"))
        self.assertNotIn("page_text", faa)
        self.assertEqual(faa["summary_method"], "model")
        self.assertEqual([s["title"] for s in result["Advanced Rail / High-Speed Rail"]], ["Siemens wins California High-Speed Rail trainset order"])
        self.assertEqual(result["Military UAS"], [])
        self.assertTrue(any(e["stage"] == "scope" for e in context.report.events))

    def test_pass_drops_a_repeat_of_an_earlier_edition_and_blocks_a_repeat_lead(self):
        story = self.story("FAA approves 12 drone corridors in Texas", "https://example.com/faa-corridors", "UAS / Drones")
        prior = groups.store_entry({**story, "page_text": ""}, "2026-10-26")
        prior["led"] = True
        result, context = self.run_pass({"UAS / Drones": [story]}, store=[prior])
        self.assertEqual(result["UAS / Drones"], [])
        self.assertTrue(any(e["stage"] == "cross-edition" for e in context.report.events))

    def test_pass_groups_two_outlets_covering_the_same_event(self):
        a = self.story("FAA approves 12 drone corridors in Texas", "https://example.com/a", "UAS / Drones")
        b = self.story("FAA approves 12 drone corridors in Texas, officials say", "https://example.org/b", "UAS / Drones", source="Reuters")
        result, _ = self.run_pass({"UAS / Drones": [a, b]})
        self.assertEqual(len(result["UAS / Drones"]), 1)
        self.assertEqual(len(result["UAS / Drones"][0]["also_covered"]), 1)
        self.assertTrue(result["UAS / Drones"][0]["lead_ok"])
        self.assertEqual(result["UAS / Drones"][0]["priority_tier"], 2)

    def test_executive_summary_fact_sheet_puts_federal_policy_first_and_marks_blocked_leads(self):
        import summary_chain

        policy = {"title": "FAA approves 12 drone corridors in Texas", "url": "https://www.faa.gov/news/corridors", "summary": "x", "importance": 3,
                  "priority_tier": 1, "lead_ok": True, "source": "FAA", "date_label": "Oct. 27, 2026"}
        company = {"title": "Joby starts piloted tests", "url": "https://joby.example/tests", "summary": "y", "importance": 9, "priority_tier": 2,
                   "lead_ok": False, "source": "Joby", "date_label": "Oct. 27, 2026"}
        sheet = summary_chain.build_fact_sheet({"Federal Policy & Implementation": [policy], "AAM / eVTOL / Advanced Aviation": [company]})
        self.assertEqual([s["url"] for s in sheet], [policy["url"], company["url"]])
        self.assertEqual(sheet[1]["lead_ok"], "no")
        rows = [{"ids": ["S2"], "paragraph": 0, "raw": "Joby began tests [S2].", "plain": "Joby began tests.", "cited": True},
                {"ids": ["S1"], "paragraph": 0, "raw": "The FAA approved corridors [S1].", "plain": "The FAA approved corridors.", "cited": True}]
        self.assertEqual(summary_chain.enforce_lead(rows, sheet)[0]["ids"], ["S1"])


if __name__ == "__main__":
    unittest.main()
