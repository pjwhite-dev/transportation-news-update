"""The live site: lead rule, store, wire, both editing lanes, the doctor and the public feed. No network, no models."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import live_doctor
import live_feed
import live_lead
import live_market
import news_engine
from live_desk import Desk, Health, desk_for, edition_running, file_story
from live_sources import Source, Wire, build_sources, load_config, rest_for
from live_store import DROPPED, FIRST_PASS, HEADLINE, QUARANTINED, QUEUED, VERIFIED, Store, iso, title_overlap, url_key

NOW = datetime(2026, 10, 30, 14, 0, tzinfo=timezone.utc)
CONFIG = load_config()

ARTICLE_BODY = (
    "The Federal Aviation Administration on Thursday approved Beta Technologies to begin passenger-carrying test "
    "flights of its ALIA electric aircraft from Burlington, Vermont. The approval covers 12 flights over the next "
    "90 days, the agency said in a statement. Beta said the flights would carry company employees only and would "
    "operate under visual flight rules during daylight hours. "
) * 4


def article_html(title: str, published: datetime, body: str = ARTICLE_BODY) -> str:
    paragraphs = "".join(f"<p>{s}.</p>" for s in body.split(". ") if s)
    return (
        f"<html><head><title>{title}</title><meta property='og:title' content='{title}'>"
        f"<meta property='article:published_time' content='{published.isoformat()}'>"
        "<script type='application/ld+json'>{\"@type\": \"NewsArticle\"}</script></head>"
        f"<body><article><h1>{title}</h1>{paragraphs}</article></body></html>"
    )


class FakeModel:
    """Answers by schema name; `fail` raises for those schemas; `calls` records each schema asked for."""

    def __init__(self, model: str = "fake", **answers):
        self.model = model
        self.answers = answers
        self.fail: set[str] = set()
        self.calls: list[str] = []
        self.lock = threading.Lock()

    def generate_structured(self, messages, *, schema_name, schema, max_output_tokens):
        with self.lock:
            self.calls.append(schema_name)
        if schema_name in self.fail:
            raise RuntimeError(f"{schema_name} down")
        answer = self.answers[schema_name]
        value = answer(messages) if callable(answer) else answer
        return SimpleNamespace(value=value)


GOOD_SUMMARY = "The Federal Aviation Administration approved Beta Technologies to begin passenger-carrying test flights of its ALIA electric aircraft. The approval covers 12 flights over the next 90 days."


def writer(summary: str = GOOD_SUMMARY, relevant: bool = True) -> FakeModel:
    return FakeModel(
        "qwen-fake",
        live_triage={"relevant": relevant, "reason": "eVTOL certification"},
        story_summary={"summary": summary},
        live_score={"impact": 4, "action": "first_commercial_operation", "reason": "first passenger test flights"},
    )


def verifier(ok: bool = True) -> FakeModel:
    return FakeModel("gemma-fake", entailment=lambda m: {"verdicts": [
        {"index": i, "supported": ok, "reason": "" if ok else "not in article"} for i in range(1, 5)]})


class LeadRuleTests(unittest.TestCase):
    def lead(self, score: int, hours: float) -> dict:
        return {"story_id": "old", "score": score, "set_at": NOW - timedelta(hours=hours)}

    def candidate(self, score: int, **extra) -> dict:
        return {"story_id": "new", "score": score, "verified": True, "published": NOW - timedelta(minutes=30), **extra}

    def test_bar_starts_above_the_lead_and_decays_toward_fifty(self):
        self.assertAlmostEqual(live_lead.bar(72, 0), 80)
        self.assertAlmostEqual(live_lead.bar(72, 12), 56.69, places=2)
        self.assertAlmostEqual(live_lead.bar(72, 24), 51.49, places=2)

    def test_a_new_story_must_clear_the_bar(self):
        self.assertFalse(live_lead.decide(self.lead(72, 2), self.candidate(70), NOW).swap)
        self.assertTrue(live_lead.decide(self.lead(72, 12), self.candidate(60), NOW).swap)

    def test_minimum_dwell_of_45_minutes(self):
        decision = live_lead.decide(self.lead(40, 0.5), self.candidate(85), NOW)
        self.assertFalse(decision.swap)
        self.assertIn("45 minutes", decision.reason)

    def test_a_90_plus_story_replaces_the_lead_after_20_minutes(self):
        self.assertTrue(live_lead.decide(self.lead(80, 0.4), self.candidate(92), NOW).swap)
        self.assertFalse(live_lead.decide(self.lead(80, 0.2), self.candidate(92), NOW).swap)

    def test_a_90_plus_lead_is_protected_for_3_hours(self):
        # The 90+ shortcut is off; the ordinary bar (93 after 1 h, 80 after 4 h) still applies.
        self.assertFalse(live_lead.decide(self.lead(91, 1), self.candidate(92), NOW).swap)
        self.assertTrue(live_lead.decide(self.lead(91, 4), self.candidate(92), NOW).swap)

    def test_only_fresh_verified_stories_lead(self):
        self.assertFalse(live_lead.decide(None, self.candidate(99, verified=False), NOW).swap)
        self.assertFalse(live_lead.decide(None, self.candidate(99, published=NOW - timedelta(hours=13)), NOW).swap)
        self.assertTrue(live_lead.decide(None, self.candidate(50), NOW).swap)

    def test_a_minor_story_never_leads_even_with_no_lead(self):
        # 2026-10-30: a profile of a Malaysian engineer (score 15) became the first lead because nothing else had led.
        self.assertFalse(live_lead.decide(None, self.candidate(15), NOW).swap)
        cleared = {"story_id": "", "score": 0, "set_at": NOW - timedelta(minutes=5)}
        self.assertFalse(live_lead.decide(cleared, self.candidate(49), NOW).swap)
        self.assertTrue(live_lead.decide(cleared, self.candidate(55), NOW).swap)

    def test_after_30_hours_the_best_story_of_the_day_takes_over(self):
        pool = [self.candidate(40, story_id="a"), self.candidate(55, story_id="b"),
                self.candidate(90, story_id="c", published=NOW - timedelta(hours=30))]
        self.assertIsNone(live_lead.stale_takeover(self.lead(70, 29), pool, NOW))
        self.assertEqual(live_lead.stale_takeover(self.lead(70, 31), pool, NOW)["story_id"], "b")
        self.assertIsNone(live_lead.stale_takeover(self.lead(70, 31), pool[:1], NOW))  # 40 is too minor to lead

    def test_significance_adds_fixed_points_and_clamps(self):
        score = live_lead.significance(impact=4, action="final_rule", pickup_outlets=5, official=True, us_relevance=10, novel=True)
        self.assertEqual(score.total, 32 + 15 + 15 + 10 + 10 + 10)
        low = live_lead.significance(impact=0, action="other", pickup_outlets=0, official=False, us_relevance=0, novel=False, penalties=["opinion"])
        self.assertEqual(low.total, 0)
        self.assertEqual(low.parts["penalty:opinion"], -15)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def test_the_same_page_found_twice_is_one_story(self):
        self.assertTrue(self.store.mark_seen("https://www.example.com/a/story/?utm=x", NOW))
        self.assertFalse(self.store.mark_seen("http://example.com/a/story", NOW))
        self.assertEqual(url_key("https://www.Example.com/A/"), "example.com/a")

    def test_visible_stories_are_newest_publication_first_in_utc(self):
        for name, published in (("a", "2026-10-30T09:00:00-04:00"), ("b", "2026-10-30T12:30:00+00:00")):
            self.store.add_story({"id": name, "url": f"https://x.com/{name}", "title": name, "published": published}, NOW)
            self.store.update(name, NOW, status=FIRST_PASS, shown_at=iso(NOW))
        self.assertEqual([s["id"] for s in self.store.visible(NOW - timedelta(days=1))], ["a", "b"])  # 13:00Z before 12:30Z

    def test_title_overlap_ignores_small_words(self):
        self.assertGreaterEqual(title_overlap("FAA approves Beta for test flights", "The FAA approves Beta test flights"), 0.8)


def fake_page(published: datetime, title: str = "FAA approves Beta Technologies passenger test flights"):
    return lambda url: (200, article_html(title, published), url)


class DeskTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.busy = False
        self.now = NOW

    def desk(self, w=None, v=None, fetch=None) -> Desk:
        return Desk(self.store, CONFIG, writer=w or writer(), verifier=v or verifier(),
                    fetch=fetch or fake_page(NOW - timedelta(hours=1)), resolve=lambda url: None,
                    clock=lambda: self.now, gpu_busy=lambda: self.busy)

    def queue(self, url: str = "https://www.flyingmag.com/faa-beta", **extra) -> str:
        return self.store.add_story({"id": url, "url": url, "title": "FAA approves Beta Technologies passenger test flights",
                                     "source": "FLYING Magazine", "published": iso(NOW - timedelta(hours=1)), **extra}, NOW)

    def test_first_pass_puts_a_checked_summary_on_the_page(self):
        sid = self.queue()
        self.assertEqual(self.desk().first_pass(sid), FIRST_PASS)
        story = self.store.get(sid)
        self.assertEqual(story["summary"], GOOD_SUMMARY)
        self.assertEqual(story["desk"], desk_for(story["section"], CONFIG))  # the edition's own section rules
        self.assertTrue(story["shown_at"])

    def test_a_summary_with_a_fact_not_in_the_article_is_never_shown(self):
        sid = self.queue()
        bad = writer("The FAA approved Beta Technologies for 40 flights in Boston.")
        self.assertEqual(self.desk(w=bad).first_pass(sid), HEADLINE)
        self.assertEqual(self.store.get(sid)["summary"], "")
        self.assertEqual(bad.calls.count("story_summary"), 2)  # one strict rewrite, then headline only

    def test_stale_or_non_article_pages_are_dropped_at_once(self):
        sid = self.queue()
        self.assertEqual(self.desk(fetch=fake_page(NOW - timedelta(days=9))).first_pass(sid), DROPPED)
        self.assertIn("stale", self.store.get(sid)["error"])

    def test_writer_says_off_topic_drops_the_story(self):
        sid = self.queue()
        self.assertEqual(self.desk(w=writer(relevant=False)).first_pass(sid), DROPPED)

    def test_writer_failures_are_counted_for_the_doctor(self):
        sid = self.queue()
        w = writer()
        w.fail.add("live_triage")
        desk = self.desk(w=w)
        self.assertEqual(desk.first_pass(sid), QUEUED)
        self.assertEqual(desk.first_pass(sid), QUEUED)
        self.assertEqual(desk.health.failures["writer"], 2)

    def test_while_the_edition_has_the_gpu_stories_wait_then_go_up_as_headlines(self):
        sid = self.queue()
        self.busy = True
        desk = self.desk()
        self.assertEqual(desk.first_pass(sid), QUEUED)
        self.assertTrue(self.store.get(sid)["record"].get("_enriched"))
        self.now = NOW + timedelta(minutes=21)
        self.assertEqual(desk.first_pass(sid), HEADLINE)

    def test_a_story_that_crashes_twice_is_quarantined(self):
        sid = self.queue()
        desk = self.desk(fetch=lambda url: (_ for _ in ()).throw(ZeroDivisionError()))
        self.assertEqual(desk.first_pass(sid), QUEUED)
        self.assertEqual(desk.first_pass(sid), QUARANTINED)

    def test_the_same_report_from_another_outlet_becomes_also_reported(self):
        first = self.queue()
        desk = self.desk()
        desk.first_pass(first)
        second = self.queue("https://evtolinsights.com/beta-faa", source="eVTOL Insights")
        self.assertEqual(desk.first_pass(second), DROPPED)
        self.assertEqual(self.store.get(first)["also"][0]["source"], "eVTOL Insights")

    def test_verified_story_is_scored_and_takes_an_empty_lead(self):
        sid = self.queue()
        desk = self.desk()
        desk.first_pass(sid)
        self.assertEqual(desk.verify(sid), VERIFIED)
        story = self.store.get(sid)
        self.assertEqual(story["score"], 32 + 15 + 0 + 0 + 10 + 10 - 10)  # impact 4, first operation, U.S., novel, one tier-3 source
        self.assertEqual(self.store.current_lead()["story_id"], sid)

    def test_a_minor_verified_story_does_not_take_an_empty_lead(self):
        sid = self.queue()
        w = writer()
        w.answers["live_score"] = {"impact": 1, "action": "plan", "reason": "profile of an engineer"}
        desk = self.desk(w=w)
        desk.first_pass(sid)
        self.assertEqual(desk.verify(sid), VERIFIED)
        self.assertIsNone(self.store.current_lead())

    def test_checker_down_keeps_first_pass_then_shows_headline_only(self):
        sid = self.queue()
        v = verifier()
        v.fail.add("entailment")
        desk = self.desk(v=v)
        desk.first_pass(sid)
        self.assertEqual(desk.verify(sid), FIRST_PASS)
        self.assertEqual(desk.verify(sid), HEADLINE)
        self.assertEqual(self.store.get(sid)["summary"], "")
        self.assertIsNone(self.store.current_lead())

    def test_checker_rejects_every_sentence_headline_only(self):
        sid = self.queue()
        desk = self.desk(v=verifier(ok=False))
        desk.first_pass(sid)
        self.assertEqual(desk.verify(sid), HEADLINE)

    def test_routine_federal_register_notices_are_dropped_without_a_model(self):
        sid = self.queue("https://www.federalregister.gov/d/2026-1", origin="Federal Register API",
                         title="Agency Information Collection Activities; Comment Request")
        w = writer()
        self.assertEqual(self.desk(w=w).first_pass(sid), DROPPED)
        self.assertEqual(w.calls, [])

    def test_a_google_link_to_a_denied_host_is_dropped_after_it_resolves(self):
        sid = self.queue("https://news.google.com/rss/articles/CBMiabc")
        desk = self.desk()
        desk.resolve = lambda url: "https://www.mordorintelligence.com/industry-reports/data-center-counter-drone"
        self.assertEqual(desk.first_pass(sid), DROPPED)
        self.assertIn("market-research", self.store.get(sid)["error"])

    def test_the_triage_model_picks_the_category(self):
        sid = self.queue()
        w = writer()
        w.answers["live_triage"] = {"relevant": True, "category": "policy", "reason": "FAA approval"}
        self.assertEqual(self.desk(w=w).first_pass(sid), FIRST_PASS)
        self.assertEqual(self.store.get(sid)["desk"], "policy")

    def test_unrecognized_stories_are_not_filed_as_drone_news(self):
        # 2026-10-30: freight rail earnings and a train-theft arrest were labelled "Drones & Airspace".
        self.assertEqual(file_story("UAS / Drones", "CPKC reports record revenue on grain and intermodal traffic by rail", CONFIG), "rail")
        self.assertEqual(file_story("UAS / Drones", "9 cartel associates arrested in California train theft ring", CONFIG), "rail")
        self.assertEqual(file_story("UAS / Drones", "Port authority names a new chief executive", CONFIG), CONFIG["desks"][-1]["key"])
        self.assertEqual(file_story("UAS / Drones", "Zipline drone delivery noise", CONFIG), "drones")
        self.assertEqual(file_story("Autonomous Vehicles", "Aurora driverless trucks", CONFIG), "av")

    def test_a_writer_outage_never_drops_a_story(self):
        # 2026-10-30: a wrong fallback tag made every call fail and 54 real stories were dropped after two "attempts".
        sid = self.queue()
        w = writer()
        w.fail = {"live_triage", "story_summary"}
        desk = self.desk(w=w)
        for _ in range(3):
            self.assertEqual(desk.first_pass(sid), QUEUED)
        story = self.store.get(sid)
        self.assertEqual(story["stage_attempts"], 0)
        self.assertIn("writer unavailable", story["error"])
        desk.level = "HEADLINES"  # the doctor's answer to a writer that stays down
        self.assertEqual(desk.first_pass(sid), HEADLINE)

    def test_market_research_listings_are_dropped_without_a_model(self):
        sid = self.queue("https://www.example.com/report", title="Data Center Counter-Drone Market Size, Share & 2032 Growth Trends Report")
        w = writer()
        self.assertEqual(self.desk(w=w).first_pass(sid), DROPPED)
        self.assertEqual(w.calls, [])

    def test_a_short_record_is_triaged_then_shown_as_headline_only(self):
        sid = self.queue("https://www.federalregister.gov/d/2026-2", origin="Federal Register API",
                         title="Normalizing Unmanned Aircraft Systems Beyond Visual Line of Sight Operations",
                         summary="The FAA proposes performance-based rules for BVLOS drone operations.")
        w = writer()
        self.assertEqual(self.desk(w=w).first_pass(sid), HEADLINE)
        self.assertEqual(w.calls, ["live_triage"])
        self.assertEqual(self.store.get(sid)["source"], "Federal Register")

    def test_sections_map_to_one_desk_each(self):
        sections = [s for d in CONFIG["desks"] for s in d["sections"]]
        self.assertEqual(len(sections), len(set(sections)))
        import news_engine

        self.assertEqual(set(sections), set(news_engine.TOPIC_SECTIONS))
        self.assertEqual(desk_for("Military UAS", CONFIG), "sec")

    def test_no_edition_lock_means_the_gpu_is_free(self):
        self.assertFalse(edition_running(Path(tempfile.gettempdir()) / "no-such-edition.lock"))


class WireTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.items = [{"title": "Waymo starts driverless rides in Denver", "url": "https://example.com/waymo", "published": iso(NOW)}]
        self.fail = False

    def fetch(self, source, since):
        if self.fail:
            raise RuntimeError("HTTP 500")
        return self.items

    def wire(self) -> Wire:
        source = Source("Trade: Example", "rss", timedelta(minutes=10), url="https://example.com/feed")
        wire = Wire(self.store, CONFIG, sources=[source], fetchers={"rss": self.fetch}, clock=lambda: NOW)
        self.store.update_source(source.name, next_due=iso(NOW))
        return wire

    def test_new_items_are_queued_once(self):
        wire = self.wire()
        self.assertEqual(wire.poll(wire.sources[0]), 1)
        self.assertEqual(wire.poll(wire.sources[0]), 0)
        self.assertEqual(self.store.counts(), {QUEUED: 1})

    def test_a_failing_source_rests_after_three_failures(self):
        wire = self.wire()
        self.fail = True
        for _ in range(3):
            wire.poll(wire.sources[0])
        state = self.store.source("Trade: Example")
        self.assertEqual(state["failures"], 3)
        self.assertEqual(state["rest_until"], iso(NOW + timedelta(minutes=5)))
        self.assertEqual(rest_for(5, CONFIG), timedelta(minutes=60))
        self.assertEqual(rest_for(2, CONFIG), timedelta(0))

    def test_social_and_market_research_hosts_are_never_queued(self):
        self.items = [{"title": "Counter-Drone Market Size, Share & 2032 Growth", "url": "https://www.linkedin.com/pulse/x", "published": iso(NOW)}]
        wire = self.wire()
        self.assertEqual(wire.poll(wire.sources[0]), 0)

    def test_every_google_query_and_feed_has_a_clock(self):
        sources = build_sources(CONFIG)
        self.assertGreater(len([s for s in sources if s.kind == "google"]), 50)
        self.assertTrue(all(s.every >= timedelta(minutes=5) for s in sources))
        self.assertNotIn("searxng", {s.kind for s in sources})  # off for the live wire (config)

    def test_agencies_whose_feeds_refuse_us_are_searched_by_site(self):
        sources = {s.name: s for s in build_sources(CONFIG)}
        for name, site in (("Federal Aviation Administration", "faa.gov"), ("U.S. Department of Transportation", "transportation.gov"), ("White House", "whitehouse.gov")):
            source = sources[f"Agency: {name}"]
            self.assertEqual((source.kind, source.site, source.official), ("google", site, True))
        self.assertFalse(any("faa.gov" in s.url or "whitehouse.gov" in s.url for s in sources.values()))
        wire = Wire(self.store, CONFIG, sources=[sources["Agency: Federal Aviation Administration"]], clock=lambda: NOW)
        with mock.patch.object(news_engine, "fetch_official_site_search", return_value=(self.items, None)) as search:
            self.assertEqual(wire.poll(wire.sources[0]), 1)
        self.assertEqual(search.call_args.args[:2], ("Federal Aviation Administration", "faa.gov"))
        self.assertTrue(self.store.by_status(QUEUED)[0]["record"]["official_source"])

    def test_site_search_drops_navigation_pages_and_names_the_agency(self):
        found = [
            {"title": "tfrweb", "source": "Federal Aviation Administration (.gov)", "url": "https://news.google.com/a"},
            {"title": "- Federal Aviation Administration (.gov)", "source": "x", "url": "https://news.google.com/b"},
            {"title": "FAA Proposes Rule to Normalize Beyond Visual Line of Sight Drone Flights", "source": "Federal Aviation Administration (.gov)", "url": "https://news.google.com/c"},
        ]
        with mock.patch.object(news_engine, "fetch_google_news", return_value=(found, None)) as google:
            items, error = news_engine.fetch_official_site_search("Federal Aviation Administration", "faa.gov", NOW, NOW)
        self.assertEqual(google.call_args.args[1], "site:faa.gov")
        self.assertIsNone(error)
        self.assertEqual([i["url"] for i in items], ["https://news.google.com/c"])
        self.assertEqual((items[0]["source"], items[0]["origin"]), ("Federal Aviation Administration", "Official site search"))


class ServiceTests(unittest.TestCase):
    def test_a_configured_model_that_is_not_installed_is_reported(self):
        import live_service
        installed = {CONFIG["desk"]["writer_model"], CONFIG["desk"]["verifier_model"]}
        self.assertEqual(live_service.missing_models(CONFIG, installed), [CONFIG["desk"]["fallback_model"]])
        self.assertEqual(live_service.missing_models(CONFIG, installed | {CONFIG["desk"]["fallback_model"]}), [])

    def test_model_calls_allow_for_a_model_load(self):
        self.assertGreaterEqual(CONFIG["doctor"]["model_stall_seconds"], 90)
        self.assertGreater(CONFIG["doctor"]["silent_after_seconds"], max(CONFIG["desk"]["budgets_seconds"].values()))


class MarketTests(unittest.TestCase):
    def watch(self, tmp, now, as_of=None):
        path = Path(tmp) / "market.json"
        if as_of:
            path.write_text(json.dumps({"as_of": as_of.isoformat(), "quotes": []}), encoding="utf-8")
        return live_market.MarketWatch(CONFIG, path, fetch=lambda: None, fallback=Path(tmp) / "none.json", clock=lambda: now)

    def test_refreshes_every_few_minutes_while_open_and_once_after_the_close(self):
        open_now = datetime(2026, 10, 29, 18, 0, tzinfo=timezone.utc)  # Thursday 2:00 p.m. ET
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(self.watch(tmp, open_now).due(open_now))  # nothing saved yet
            self.assertFalse(self.watch(tmp, open_now, open_now - timedelta(minutes=2)).due(open_now))
            self.assertTrue(self.watch(tmp, open_now, open_now - timedelta(minutes=6)).due(open_now))
            evening = datetime(2026, 10, 30, 1, 0, tzinfo=timezone.utc)  # Thursday 9:00 p.m. ET
            self.assertTrue(self.watch(tmp, evening, open_now).due(evening))  # the close is not saved yet
            self.assertFalse(self.watch(tmp, evening, evening - timedelta(hours=3)).due(evening))
            saturday = datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc)
            friday_close = datetime(2026, 10, 30, 21, 0, tzinfo=timezone.utc)  # Friday 5:00 p.m. ET
            self.assertTrue(self.watch(tmp, saturday, evening).due(saturday))  # Friday's close is still owed
            self.assertFalse(self.watch(tmp, saturday, friday_close).due(saturday))  # then it rests all weekend

    def test_a_failed_fetch_waits_before_retrying_and_keeps_nothing(self):
        now = datetime(2026, 10, 29, 18, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            watch = self.watch(tmp, now)
            self.assertFalse(watch.refresh())
            self.assertFalse(watch.due(now + timedelta(minutes=1)))
            self.assertIsNone(watch.current())

    def test_the_page_gets_prices_and_a_dated_label_only(self):
        snapshot = {"as_of": "2026-10-29T18:00:00+00:00", "session_label": "Intraday, Thu., Oct. 29", "source": "Finnhub",
                    "quotes": [{"symbol": "JOBY", "name": "Joby Aviation", "group": "eVTOL", "price": 9.1, "change": 0.2, "percent": 2.25, "quote_time": "x"}]}
        out = live_market.public_market(snapshot, datetime(2026, 10, 29, 18, 0, tzinfo=timezone.utc))
        self.assertTrue(out["open"])
        self.assertEqual(out["quotes"][0], {"symbol": "JOBY", "name": "Joby Aviation", "group": "eVTOL", "price": 9.1, "change": 0.2, "percent": 2.25})
        self.assertIsNone(live_market.public_market({**snapshot, "session_label": ""}, NOW))


class DoctorTests(unittest.TestCase):
    def level(self, **signals):
        base = dict(writer_failures=0, verifier_failures=0, oldest_unverified=None, gpu_busy=False, feed_ok=True, now=NOW, config=CONFIG)
        return live_doctor.compute_level(**{**base, **signals})[0]

    def test_levels_step_down_in_order(self):
        self.assertEqual(self.level(), "LIVE")
        self.assertEqual(self.level(verifier_failures=3), "LITE")
        self.assertEqual(self.level(oldest_unverified=NOW - timedelta(minutes=31)), "LITE")
        self.assertEqual(self.level(gpu_busy=True), "LITE")
        self.assertEqual(self.level(writer_failures=3, verifier_failures=3), "HEADLINES")
        self.assertEqual(self.level(feed_ok=False, writer_failures=3), "FROZEN")

    def test_writer_switches_to_the_fallback_and_back(self):
        store = Store(":memory:")
        primary, fallback = writer(), writer()
        desk = Desk(store, CONFIG, writer=primary, verifier=verifier(), fetch=fake_page(NOW), resolve=lambda u: None, clock=lambda: NOW, gpu_busy=lambda: False)
        with tempfile.TemporaryDirectory() as tmp:
            doctor = live_doctor.Doctor(store, desk, CONFIG, primary_writer=primary, fallback_writer=fallback, heartbeat_path=Path(tmp) / "hb.json", clock=lambda: NOW)
            desk.health.failures["writer"] = 2
            doctor.manage_writer(NOW)
            self.assertIs(desk.writer, fallback)
            doctor.manage_writer(NOW + timedelta(minutes=16))
            self.assertIs(desk.writer, primary)

    def test_a_crashed_worker_is_restarted(self):
        store = Store(":memory:")
        desk = Desk(store, CONFIG, writer=writer(), verifier=verifier(), fetch=fake_page(NOW), resolve=lambda u: None, clock=lambda: NOW, gpu_busy=lambda: False)
        runs = []

        def flaky(stop, beat):
            runs.append(1)
            if len(runs) == 1:
                raise RuntimeError("boom")
            stop.wait(5)

        with tempfile.TemporaryDirectory() as tmp:
            doctor = live_doctor.Doctor(store, desk, CONFIG, primary_writer=desk.writer, fallback_writer=None, heartbeat_path=Path(tmp) / "hb.json")
            doctor.add_worker("flaky", flaky)
            doctor.start()
            doctor.workers["flaky"].thread.join(2)
            doctor.supervise()
            doctor.stop.set()
            self.assertEqual(doctor.workers["flaky"].restarts, 1)
            self.assertEqual(len(runs), 2)


class FeedTests(unittest.TestCase):
    def test_the_public_feed_has_no_article_text_or_scores(self):
        store = Store(":memory:")
        desk = Desk(store, CONFIG, writer=writer(), verifier=verifier(), fetch=fake_page(NOW - timedelta(hours=1)), resolve=lambda u: None, clock=lambda: NOW, gpu_busy=lambda: False)
        sid = store.add_story({"id": "s", "url": "https://www.flyingmag.com/faa-beta", "title": "FAA approves Beta Technologies passenger test flights", "source": "FLYING", "published": iso(NOW - timedelta(hours=1))}, NOW)
        desk.first_pass(sid)
        desk.verify(sid)
        feed = live_feed.build_feed(store, CONFIG, now=NOW, level="LIVE")
        text = json.dumps(feed)
        self.assertNotIn("score", text)
        self.assertNotIn("passenger-carrying test flights of its ALIA electric aircraft from Burlington", text)
        self.assertEqual(feed["lead"]["id"], "s")
        self.assertEqual(feed["stories"][0]["check"], "checked")
        self.assertEqual(feed["ticker"][0]["source"], "FLYING")
        self.assertEqual(live_feed.content_hash(feed), live_feed.content_hash({**feed, "generated": "later", "wire": [1]}))

    def test_minor_stories_stay_out_of_developing(self):
        store = Store(":memory:")
        for sid, score in (("big", 62), ("minor", 15)):
            store.add_story({"id": sid, "url": f"https://example.com/{sid}", "title": f"Story {sid}", "published": iso(NOW - timedelta(hours=1))}, NOW)
            store.update(sid, NOW, status=VERIFIED, score=score, shown_at=iso(NOW), desk="air")
        feed = live_feed.build_feed(store, CONFIG, now=NOW, level="LIVE")
        self.assertEqual(feed["developing"], ["big"])
        self.assertEqual({s["id"] for s in feed["stories"]}, {"big", "minor"})  # both stay in the feed
        self.assertTrue(feed["site"]["tagline"])

    def test_write_site_copies_the_page_and_writes_the_feed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = live_feed.write_site(Path(tmp), {"version": 1})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"version": 1})
            for name in ("index.html", "live.js", "live.css"):
                self.assertTrue((Path(tmp) / name).exists())

    def test_the_page_never_says_verified(self):
        # We check our summary against the article; we do not verify the reporting (owner, 2026-10-30).
        for name in ("index.html", "live.js", "live.css"):
            self.assertNotIn("verif", (live_feed.ASSETS / name).read_text(encoding="utf-8").casefold(), name)


if __name__ == "__main__":
    unittest.main()
