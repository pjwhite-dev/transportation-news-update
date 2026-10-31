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

import live_days
import live_doctor
import live_feed
import live_lead
import live_market
import live_posts
import news_engine
import requests

from live_desk import Desk, Health, date_only, desk_for, edition_running, file_story, gpu_paused, settle_published, triage_schema
from live_override import OverrideDesk, parse_request
from live_push import FeedPusher
from live_sort import Sorter, candidates
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


class PublishedTimeTests(unittest.TestCase):
    """A source that gives only a date must not show as 12:00 p.m. or jump above newer stories."""
    noon_today = datetime(2026, 10, 30, 16, 0, tzinfo=timezone.utc)  # noon Eastern, how parse_date stamps a bare date

    def test_a_bare_date_never_replaces_a_feed_time_from_that_day(self):
        feed = NOW - timedelta(minutes=30)
        self.assertTrue(date_only(self.noon_today))
        self.assertEqual(settle_published(self.noon_today, feed, NOW), (feed, False))

    def test_a_bare_date_for_today_is_never_later_than_when_we_found_it(self):
        self.assertEqual(settle_published(self.noon_today, None, NOW), (NOW, True))

    def test_a_bare_date_from_an_earlier_day_keeps_that_day(self):
        yesterday = self.noon_today - timedelta(days=1)
        self.assertEqual(settle_published(yesterday, None, NOW), (yesterday, True))

    def test_a_real_page_time_is_kept(self):
        page = NOW - timedelta(hours=2, minutes=7)
        self.assertEqual(settle_published(page, NOW - timedelta(hours=1), NOW), (page, False))

    def test_the_store_never_dates_a_story_after_it_was_found(self):
        store = Store(":memory:")
        sid = store.add_story({"url": "https://example.com/a", "title": "A story", "published": iso(self.noon_today)}, NOW)
        self.assertEqual(store.get(sid)["published"], iso(NOW))

    def test_the_feed_marks_a_date_without_a_time(self):
        story = {"id": "s", "title": "T", "summary": "", "url": "u", "source": "S", "desk": "air", "section": "",
                 "published": iso(NOW), "found_at": iso(NOW), "shown_at": iso(NOW), "status": FIRST_PASS,
                 "official": 0, "also": [], "notes": [], "record": {"_date_only": True}}
        out = live_feed.public_story(story)
        self.assertFalse(out["time_known"])
        self.assertEqual(out["found"], iso(NOW))
        self.assertTrue(live_feed.public_story({**story, "record": {}})["time_known"])


class FakeResponse:
    def __init__(self, data=None, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeGitHub:
    def __init__(self, issues):
        self.issues, self.posts, self.patches = issues, [], []

    def get(self, url, **kw):
        return FakeResponse(self.issues)

    def post(self, url, **kw):
        self.posts.append((url, kw["json"]["body"]))
        return FakeResponse({})

    def patch(self, url, **kw):
        self.patches.append(url)
        return FakeResponse({})


class OverrideRequestTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.desk = Desk(self.store, CONFIG, writer=writer(), verifier=verifier(), fetch=fake_page(NOW - timedelta(hours=1)),
                         resolve=lambda url: None, clock=lambda: NOW, gpu_busy=lambda: False)
        self.sid = self.store.add_story({"id": "story-one", "url": "https://www.flyingmag.com/faa-beta",
                                         "title": "FAA approves Beta Technologies passenger test flights",
                                         "source": "FLYING", "published": iso(NOW - timedelta(hours=1))}, NOW)
        self.desk.first_pass(self.sid)

    def issue(self, number, body, login="pjwhite-dev", title="Headline override: FAA approves Beta", age=timedelta(minutes=2)):
        return {"number": number, "title": title, "body": body, "user": {"login": login},
                "created_at": iso(NOW - age).replace("+00:00", "Z")}

    def run_check(self, issues):
        github = FakeGitHub(issues)
        desk = OverrideDesk(self.desk, CONFIG, session=github, token=lambda: "test-token", clock=lambda: NOW)
        return desk.check(), github

    def test_the_owners_request_is_applied_answered_and_closed(self):
        applied, github = self.run_check([self.issue(7, f"story: {self.sid}\nhours: 12\n\nLead for 12 h")])
        self.assertEqual(applied, 1)
        self.assertEqual(self.store.current_lead()["story_id"], self.sid)
        self.assertIn("Done:", github.posts[0][1])
        self.assertEqual(len(github.patches), 1)

    def test_requests_from_anyone_else_are_ignored(self):
        applied, github = self.run_check([self.issue(8, f"story: {self.sid}\nhours: 12", login="someone-else")])
        self.assertEqual(applied, 0)
        self.assertIsNone(self.store.current_lead())
        self.assertEqual(github.posts, [])

    def test_bad_or_old_requests_are_answered_but_not_applied(self):
        applied, github = self.run_check([
            self.issue(9, f"story: {self.sid}\nhours: 60"),
            self.issue(10, "make it the lead please"),
            self.issue(11, f"story: {self.sid}\nhours: 4", age=timedelta(hours=7)),
        ])
        self.assertEqual(applied, 0)
        self.assertIsNone(self.store.current_lead())
        self.assertEqual(len(github.posts), 3)
        self.assertTrue(all(body.startswith("Not applied") for _, body in github.posts))

    def test_a_request_is_handled_once(self):
        issue = self.issue(12, f"story: {self.sid}\nhours: 3")
        self.run_check([issue])
        applied, github = self.run_check([issue])  # closing failed upstream; it is still listed
        self.assertEqual(applied, 0)
        self.assertEqual(github.posts, [])

    def test_parse_request(self):
        self.assertEqual(parse_request("story: abc123\nhours: 0"), ("abc123", 0))
        self.assertIsNone(parse_request("story: abc123"))
        # A story stored without a wire id is keyed by its address.
        self.assertEqual(parse_request("story: faa.gov/newsroom/a-release\nhours: 12"), ("faa.gov/newsroom/a-release", 12))
        self.assertIsNone(parse_request("story: two words\nhours: 12"))


class PublicPageTests(unittest.TestCase):
    def test_the_public_page_has_no_login_link(self):
        page = Path("site_assets/live/index.html").read_text(encoding="utf-8") + Path("site_assets/live/live.js").read_text(encoding="utf-8")
        self.assertNotIn("login", page.lower())

    def test_the_page_can_be_shared_paused_and_linked_into(self):
        page = Path("site_assets/live/index.html").read_text(encoding="utf-8")
        script = Path("site_assets/live/live.js").read_text(encoding="utf-8")
        style = Path("site_assets/live/live.css").read_text(encoding="utf-8")
        for tag in ('property="og:title"', 'property="og:image" content="https://advancedtransportation.live/og-card.png"', 'name="twitter:card"'):
            self.assertIn(tag, page)
        self.assertTrue(Path("site_assets/live/og-card.png").exists())
        self.assertIn('id="motion"', page)  # a Pause button for readers whose device does not ask for less motion
        self.assertIn(".still .ticker-track{animation:none}", style)
        self.assertIn('"#s-"', script)  # every story has an address
        self.assertIn("view=all", script)
        self.assertNotIn("verif", (page + script).lower())

    def test_search_dark_mode_and_clear_times(self):
        page = Path("site_assets/live/index.html").read_text(encoding="utf-8")
        script = Path("site_assets/live/live.js").read_text(encoding="utf-8")
        style = Path("site_assets/live/live.css").read_text(encoding="utf-8")
        days = Path("site_assets/days/index.html").read_text(encoding="utf-8") + Path("site_assets/days/days.js").read_text(encoding="utf-8")
        self.assertIn('role="search"', page)
        self.assertIn('href="days/"', page)  # Past days
        self.assertIn("q=", script)  # a search can be shared
        self.assertIn("@media (prefers-color-scheme:dark)", style)
        light = style.split("@media (prefers-color-scheme:dark)")[0]
        self.assertNotIn("background:#fff", style.replace("--paper:#fff", ""))  # every surface follows the theme
        self.assertIn("--paper:#fff", light)
        self.assertIn(" h ago", script)  # today's stories: how long ago, the exact time on hover
        self.assertIn("node.title", script)
        self.assertNotIn('"found "', script)  # "found 12:07 a.m." read like a publication time
        self.assertIn("No time given", script)
        self.assertNotIn("verif", days.lower())
        self.assertNotIn("login", days.lower())

    def test_the_login_page_reads_the_same_feed_and_never_says_verified(self):
        login = Path("site_assets/login/index.html").read_text(encoding="utf-8")
        live = Path("site_assets/live/index.html").read_text(encoding="utf-8")
        feed = 'name="live-feed" content="https://raw.githubusercontent.com/pjwhite-dev/transportation-news-update/live-feed/feed.json"'
        self.assertIn(feed, login)
        self.assertIn(feed, live)
        self.assertIn('name="robots" content="noindex', login)
        self.assertNotIn("verif", login.lower())

    def test_the_login_page_opens_on_sign_in_and_shows_the_tools_only_to_the_owner(self):
        login = Path("site_assets/login/index.html").read_text(encoding="utf-8")
        config = load_config()
        self.assertIn(f'name="override-owner" content="{config["override"]["owner"]}"', login)
        self.assertIn(f'name="override-repo" content="{config["override"]["repo"]}"', login)
        self.assertRegex(login, r'<main class="wrap signin" id="signin">')
        self.assertRegex(login, r'<main class="wrap" id="tools" hidden>')  # the selector starts hidden
        self.assertIn('github("/user")', login)
        script = login[login.index("<script>"):]
        # The story list is fetched only after GitHub names the owner, never as the page opens.
        self.assertLess(script.index("user.login).toLowerCase() !== OWNER"), script.index("loadFeed();"))

    def test_the_live_page_adds_companies_without_changing_the_edition_list(self):
        import live_service
        import market_watch
        symbols = [s for s, _, _ in live_service.live_watchlist(CONFIG)]
        self.assertEqual(symbols[: len(market_watch.WATCHLIST)], [s for s, _, _ in market_watch.WATCHLIST])
        self.assertIn("TSLA", symbols)
        self.assertEqual(len(symbols), len(set(symbols)))


class OfficialStripTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def item(self, org, title, hours=1, url=None):
        return live_posts.post_item(org, title, url or f"https://example.com/{org}/{hours}/{len(title)}", iso(NOW - timedelta(hours=hours)), "Press release")

    def test_site_search_clutter_is_left_out(self):
        for title in ("Autonomy Engineer Intern, 2027 - Skydio", "Contact us - BNSF Railway", "tfrweb - Federal Aviation Administration (.gov)",
                      "Full Self-Driving (Supervised) Referral Program - Tesla", "CSX Customer Advisory: Toledo, Ohio, Incident"):
            self.assertFalse(live_posts.keep(title), title)
        self.assertTrue(live_posts.keep("Zipline Now Making Quieter Deliveries with Acoustics Breakthrough"))
        for title in ("RNAV (GPS) RWY 18 RNAV (GPS) RWY 18", "WAZKO ONE ARRIVAL WAZKO ONE ARRIVAL",
                      "G:\\GRAPHICS\\TAPS\\United States\\Gulfport Biloxi Intl KGPT\\hi_ils_y_or_loc"):
            self.assertFalse(live_posts.keep(title), title)

    def test_an_organizations_name_does_not_make_its_items_relevant(self):
        items = [self.item("U.S. Department of Defense", "Army Mountain Division Takes on Panamanian Jungle"),
                 self.item("U.S. Department of Defense", "Army fields new counter-drone interceptor at Fort Sill")]
        self.assertEqual([i["title"] for i in live_posts.relevant(items)], ["Army fields new counter-drone interceptor at Fort Sill"])

    def test_every_google_search_gets_a_turn(self):
        early = Source("Official: NTSB", "google", timedelta(minutes=60), site="ntsb.gov", posts=True)
        late = Source("Google News: drone delivery", "google", timedelta(minutes=30), query="drone delivery")
        wire = Wire(self.store, CONFIG, sources=[late, early], fetchers={"google": lambda s, since: []}, clock=lambda: NOW)
        self.store.update_source(early.name, next_due=iso(NOW - timedelta(minutes=20)))
        self.store.update_source(late.name, next_due=iso(NOW - timedelta(minutes=1)))
        self.assertEqual(wire.due()[0].name, "Official: NTSB")

    def test_record_keeps_a_week_newest_first_and_never_twice(self):
        old = self.item("FAA", "FAA issues a final rule on drone operations", hours=24 * 8)
        a, b = self.item("FAA", "FAA approves new air taxi test corridor", 3), self.item("NTSB", "NTSB opens an investigation into a rail collision", 1)
        self.assertEqual(live_posts.record(self.store, [old, a], NOW), 2)
        self.assertEqual(live_posts.record(self.store, [a, b], NOW), 1)
        self.assertEqual([i["org"] for i in live_posts.public_posts(self.store, NOW)], ["NTSB", "FAA"])

    def test_no_organization_fills_the_strip(self):
        live_posts.record(self.store, [self.item("Boeing", f"Boeing announces delivery number {n} of its freighter", n) for n in range(1, 6)]
                          + [self.item("Amtrak", "Amtrak opens the new Portal North Bridge track", 7)], NOW)
        orgs = [i["org"] for i in live_posts.public_posts(self.store, NOW)]
        self.assertEqual(orgs.count("Boeing"), 2)
        self.assertIn("Amtrak", orgs)

    def test_bluesky_keeps_the_accounts_own_posts_only(self):
        handle = "mta.info"
        feed = {"feed": [
            {"post": {"uri": "at://did:plc:x/app.bsky.feed.post/abc", "author": {"handle": handle},
                      "record": {"createdAt": "2026-10-30T13:00:00Z", "text": "Service on the 7 line resumes after signal repairs tonight"}}},
            {"reason": {"$type": "repost"}, "post": {"uri": "at://other/app.bsky.feed.post/r1", "author": {"handle": "someone.bsky.social"},
                      "record": {"createdAt": "2026-10-30T13:00:00Z", "text": "A repost of another account's long post here"}}},
            {"post": {"uri": "at://did:plc:x/app.bsky.feed.post/old", "author": {"handle": handle},
                      "record": {"createdAt": "2026-10-01T13:00:00Z", "text": "An old post from earlier this month about service"}}},
        ]}
        session = SimpleNamespace(get=lambda *a, **k: FakeResponse(feed))
        items = live_posts.fetch_bluesky(handle, "MTA", NOW - timedelta(days=7), session)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://bsky.app/profile/mta.info/post/abc")
        self.assertEqual(items[0]["kind"], "Bluesky post")

    def test_strip_sources_never_queue_stories_and_official_ones_feed_the_strip(self):
        found = {"title": "FAA approves BVLOS waiver for drone delivery in Texas", "url": "https://www.faa.gov/news/1",
                 "published": iso(NOW - timedelta(hours=2)), "source": "FAA"}
        sources = [Source("Official: NTSB", "google", timedelta(minutes=60), site="ntsb.gov", official=True, posts=True, topical=False),
                   Source("Agency: Federal Aviation Administration", "google", timedelta(minutes=10), site="faa.gov", official=True)]
        wire = Wire(self.store, CONFIG, sources=sources, fetchers={"google": lambda s, since: [dict(found, url=found["url"] + s.site, title=f"{found['title']} ({s.site})")]}, clock=lambda: NOW)
        self.assertEqual(wire.poll(sources[0]), 0)
        self.assertEqual(self.store.counts().get("queued", 0), 0)
        wire.poll(sources[1])
        self.assertEqual(self.store.counts().get("queued", 0), 1)  # the wire's own official source still queues its story
        self.assertEqual({i["org"] for i in live_posts.public_posts(self.store, NOW)}, {"NTSB", "Federal Aviation Administration"})

    def test_every_strip_source_is_domain_verified_on_bluesky(self):
        for spec in CONFIG["posts"]["sources"]:
            if spec["kind"] == "bluesky":
                self.assertNotIn(".bsky.social", spec["handle"])


class FeedPushTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "out").mkdir()
        (self.dir / "wt" / ".git").mkdir(parents=True)
        self.calls = []
        self.now = NOW

    def pusher(self, fail=""):
        def run(args, cwd):
            self.calls.append(args[0])
            failed = args[0] == fail
            return SimpleNamespace(returncode=1 if failed else 0, stdout="", stderr="boom" if failed else "")
        config = {**CONFIG, "push": {**CONFIG["push"], "worktree": str(self.dir / "wt")}}
        return FeedPusher(config, self.dir / "out", self.dir, run=run, clock=lambda: self.now)

    def write_feed(self, title):
        (self.dir / "out" / "feed.json").write_text(json.dumps({"generated": iso(self.now), "stories": [{"title": title}]}), encoding="utf-8")

    def test_pushes_when_stories_change_but_not_more_often_than_the_gap(self):
        pusher = self.pusher()
        self.write_feed("a")
        self.assertTrue(pusher.push_once())
        self.assertEqual(self.calls, ["add", "commit", "push"])
        self.now += timedelta(seconds=60)
        self.write_feed("b")
        self.assertFalse(pusher.push_once())  # changed, but only a minute later
        self.now += timedelta(seconds=150)
        self.assertTrue(pusher.push_once())

    def test_a_heartbeat_push_goes_out_even_when_nothing_changed(self):
        pusher = self.pusher()
        self.write_feed("a")
        pusher.push_once()
        self.now += timedelta(seconds=300)
        self.assertFalse(pusher.push_once())
        self.now += timedelta(seconds=301)
        self.assertTrue(pusher.push_once())

    def test_a_failed_push_is_retried_and_reported(self):
        pusher = self.pusher(fail="push")
        self.write_feed("a")
        self.assertFalse(pusher.push_once())
        self.assertIn("git push failed", pusher.error)
        self.assertIsNone(pusher.last_push)

    def test_overnight_the_page_updates_once_an_hour_on_the_hour(self):
        self.now = datetime(2026, 10, 31, 3, 50, tzinfo=timezone.utc)  # 11:50 p.m. Eastern (EDT)
        pusher = self.pusher()
        self.write_feed("a")
        self.assertTrue(pusher.push_once())
        self.now += timedelta(minutes=10, seconds=5)  # 12:00 a.m.: the first hourly push, though nothing changed
        self.assertTrue(pusher.push_once())
        for minutes in (5, 20, 59):  # changes and heartbeats wait for the next hour
            self.now = datetime(2026, 10, 31, 4, minutes, tzinfo=timezone.utc)
            self.write_feed(f"b{minutes}")
            self.assertFalse(pusher.push_once())
        self.now = datetime(2026, 10, 31, 5, 0, 30, tzinfo=timezone.utc)  # 1 a.m.
        self.assertTrue(pusher.push_once())
        self.now = datetime(2026, 10, 31, 8, 0, 10, tzinfo=timezone.utc)  # 4 a.m.: back to every few minutes
        self.assertTrue(pusher.push_once())
        self.write_feed("c")
        self.now += timedelta(minutes=3)
        self.assertTrue(pusher.push_once())

    def test_the_feed_tells_the_page_the_overnight_hours(self):
        self.assertEqual(CONFIG["push"]["overnight"], {"from_hour": 0, "to_hour": 4})
        script = Path("site_assets/live/live.js").read_text(encoding="utf-8")
        self.assertIn("feed.overnight", script)
        self.assertIn("once an hour", script)

    def test_the_daily_archive_goes_out_with_the_feed_when_it_changed(self):
        self.write_feed("one")
        days = self.dir / "out" / "days"
        days.mkdir()
        (days / "2026-10-29.json").write_text("{}", encoding="utf-8")
        (days / "index.html").write_text("page", encoding="utf-8")  # the page itself is published with the site, not here
        adds = []
        pusher = self.pusher()
        pusher.run_git = lambda args, cwd: (adds.append(args) if args[0] == "add" else None) or SimpleNamespace(returncode=0, stdout="", stderr="")
        self.assertTrue(pusher.push_once())
        self.assertEqual(adds[-1], ["add", "feed.json", "days"])
        self.assertEqual(sorted(p.name for p in (self.dir / "wt" / "days").iterdir()), ["2026-10-29.json"])
        self.now += timedelta(hours=1)
        self.write_feed("two")
        self.assertTrue(pusher.push_once())
        self.assertEqual(adds[-1], ["add", "feed.json"])  # unchanged days are left alone

    def test_the_push_never_forces(self):
        source = Path("live_push.py").read_text(encoding="utf-8")
        self.assertNotIn("--force", source)
        self.assertNotIn("+{BRANCH}", source)


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

    def test_a_refused_faa_release_goes_up_as_headline_and_link_only(self):
        url = "https://www.faa.gov/newsroom/secretary-marks-evtol-pilot-program-milestone"
        sid = self.queue(url, title="Secretary Marks Major Milestone in eVTOL Pilot Program | Federal Aviation Administrat",
                         source="Federal Aviation Administration", official_source=True)
        w = writer()
        self.assertEqual(self.desk(w=w, fetch=lambda u: (403, "<html><h1>Access Denied</h1></html>", u)).first_pass(sid), HEADLINE)
        story = self.store.get(sid)
        self.assertEqual(story["title"], "Secretary Marks Major Milestone in eVTOL Pilot Program")
        self.assertEqual(story["summary"], "")
        self.assertEqual(story["published"], iso(NOW - timedelta(hours=1)))  # the news search's date
        self.assertEqual(w.calls, ["live_triage"])  # judged on its headline; nothing read, so nothing summarized
        self.assertTrue(story["shown_at"])

    def test_a_refused_release_off_the_portfolio_or_outside_the_newsroom_is_dropped(self):
        refuse = lambda u: (403, "", u)
        grants = self.queue("https://www.transportation.gov/briefing-room/highway-safety-grants", title="Secretary Invests $1 Billion in Highway Safety Grants")
        self.assertEqual(self.desk(w=writer(relevant=False), fetch=refuse).first_pass(grants), DROPPED)
        notice = self.queue("https://www.faa.gov/air_traffic/publications/domesticnotices/dom26052_af.html", title="Regional Airport incorrect airport diagram notice")
        self.assertEqual(self.desk(fetch=refuse).first_pass(notice), DROPPED)

    def test_only_listed_agencies_and_only_refusals_and_only_recent_get_headline_only(self):
        refuse = lambda u: (403, "", u)
        other = self.queue("https://www.example-news.com/faa-story")
        self.assertEqual(self.desk(fetch=refuse).first_pass(other), DROPPED)  # not an agency on the list
        gone = self.queue("https://www.faa.gov/newsroom/gone-release")
        self.assertEqual(self.desk(fetch=lambda u: (404, "", u)).first_pass(gone), DROPPED)  # missing, not refused
        old = self.queue("https://www.transportation.gov/briefing-room/old-release", published=iso(NOW - timedelta(days=9)))
        self.assertEqual(self.desk(fetch=refuse).first_pass(old), DROPPED)  # the search's date is too old

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

    def two_pages(self):
        """The Beta story, plus a different minor feature at /minor-feature."""
        beta, minor = fake_page(NOW - timedelta(hours=1)), fake_page(NOW - timedelta(hours=1), "Profile: a drone hobbyist builds a hangar")
        return lambda url: (minor if "minor" in url else beta)(url)

    def test_the_owner_can_force_a_minor_story_to_lead_and_it_holds(self):
        big, minor = self.queue(), self.queue("https://example.com/minor-feature")
        desk = self.desk(fetch=self.two_pages())
        desk.first_pass(minor)
        desk.force_lead(minor, 6)
        self.assertEqual(self.store.current_lead()["story_id"], minor)
        desk.first_pass(big)
        self.assertEqual(desk.verify(big), VERIFIED)  # scores 57: would take a minor lead, but the owner's choice holds
        self.assertEqual(self.store.current_lead()["story_id"], minor)
        feed = live_feed.build_feed(self.store, CONFIG, now=NOW, level="LIVE")
        self.assertEqual(feed["lead"]["held_until"], iso(NOW + timedelta(hours=6)))

    def test_when_the_override_ends_the_best_qualifying_story_leads(self):
        big, minor = self.queue(), self.queue("https://example.com/minor-feature")
        desk = self.desk(fetch=self.two_pages())
        desk.first_pass(minor)
        desk.force_lead(minor, 2)
        desk.first_pass(big)
        desk.verify(big)
        self.now = NOW + timedelta(hours=2, minutes=1)
        self.assertTrue(desk.check_stale_lead())
        self.assertEqual(self.store.current_lead()["story_id"], big)
        self.assertIsNone(desk.override())

    def test_ending_the_override_early_with_no_qualifying_story_clears_the_lead(self):
        minor = self.queue("https://example.com/minor-feature")
        desk = self.desk()
        desk.first_pass(minor)
        desk.force_lead(minor, 48)
        self.assertEqual(desk.force_lead(minor, 0), "override ended")
        self.assertEqual(self.store.current_lead()["story_id"], "")

    def test_override_hours_and_story_are_checked(self):
        sid = self.queue()
        desk = self.desk()
        desk.first_pass(sid)
        for hours in (49, -1):
            with self.assertRaises(ValueError):
                desk.force_lead(sid, hours)
        with self.assertRaises(ValueError):
            desk.force_lead("not-a-story", 4)

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
        w.answers["live_triage"] = {"relevant": True, "category": "air", "reason": "FAA approval"}
        self.assertEqual(self.desk(w=w).first_pass(sid), FIRST_PASS)
        self.assertEqual(self.store.get(sid)["desk"], "air")

    def test_there_is_no_policy_desk_policy_goes_on_the_desk_it_is_about(self):
        # Owner, 2026-10-30: "California Lawmakers ... Reign In Autonomous Vehicles" is Autonomous Vehicles.
        self.assertNotIn("policy", [d["key"] for d in CONFIG["desks"]])
        self.assertNotIn("policy", triage_schema(CONFIG)["properties"]["category"]["enum"])
        cases = {
            "California Lawmakers and Drivers Pressure Regulators to Reign In Autonomous Vehicles": "av",
            "FCC Opens 800 MHz Spectrum to Drones as Part 108 Deadline Nears": "drones",
            "FAA Official Says Commercial eVTOL Flights Before Type Certification Would Be Very Limited": "air",
            "FCC proposes spectrum changes for counter-UAS detection": "sec",
            "Blaine receives $66 million to build overpass at Bell Road rail crossing": "rail",
            "White House makes Section 232 determination regarding foreign-made robots": "robot",
        }
        for title, key in cases.items():
            self.assertEqual(file_story("Federal Policy & Implementation", title, CONFIG), key, title)

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
        sections = [s for d in CONFIG["desks"] for s in d["sections"]] + CONFIG["topic_sections"]
        self.assertEqual(len(sections), len(set(sections)))
        import news_engine

        self.assertEqual(set(sections), set(news_engine.TOPIC_SECTIONS))
        self.assertEqual(desk_for("Military UAS", CONFIG), "sec")

    def test_no_edition_lock_means_the_gpu_is_free(self):
        self.assertFalse(edition_running(Path(tempfile.gettempdir()) / "no-such-edition.lock"))

    def test_the_owners_gpu_pause_lasts_until_its_time_then_lapses(self):
        path = Path(tempfile.mkdtemp()) / "gpu_pause.json"
        self.assertFalse(gpu_paused(NOW, path))  # no file
        path.write_text(json.dumps({"until": iso(NOW + timedelta(hours=1))}), encoding="utf-8")
        self.assertTrue(gpu_paused(NOW, path))
        self.assertFalse(gpu_paused(NOW + timedelta(hours=1), path))  # one time only: it lapses by itself
        path.write_text("not json", encoding="utf-8")
        self.assertFalse(gpu_paused(NOW, path))


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
        self.assertEqual(feed["stories"][0]["impact"], "significant")  # scored 57: a word, never the number

    def test_impact_words_follow_the_lead_thresholds(self):
        self.assertEqual([live_lead.impact_tier(s) for s in (None, 0, 29, 30, 49, 50, 100)],
                         ["", "minor", "minor", "notable", "notable", "significant", "significant"])

    def test_an_unscored_story_has_no_impact_word(self):
        store = Store(":memory:")
        desk = Desk(store, CONFIG, writer=writer(), verifier=verifier(), fetch=fake_page(NOW - timedelta(hours=1)), resolve=lambda u: None, clock=lambda: NOW, gpu_busy=lambda: False)
        sid = store.add_story({"id": "s", "url": "https://www.flyingmag.com/faa-beta", "title": "FAA approves Beta Technologies passenger test flights", "source": "FLYING", "published": iso(NOW - timedelta(hours=1))}, NOW)
        desk.first_pass(sid)
        feed = live_feed.build_feed(store, CONFIG, now=NOW, level="LIVE")
        self.assertNotIn("impact", feed["stories"][0])

    def test_minor_stories_stay_out_of_developing(self):
        store = Store(":memory:")
        for sid, score in (("big", 62), ("minor", 15)):
            store.add_story({"id": sid, "url": f"https://example.com/{sid}", "title": f"Story {sid}", "published": iso(NOW - timedelta(hours=1))}, NOW)
            store.update(sid, NOW, status=VERIFIED, score=score, shown_at=iso(NOW), desk="air")
        feed = live_feed.build_feed(store, CONFIG, now=NOW, level="LIVE")
        self.assertEqual(feed["developing"], ["big"])
        self.assertEqual({s["id"] for s in feed["stories"]}, {"big", "minor"})  # both stay in the feed

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


class DaysTests(unittest.TestCase):
    """The daily archive (/days/): one file per finished Eastern day, the same grouping as the live page."""

    def setUp(self):
        self.store = Store(":memory:")
        self.dir = Path(tempfile.mkdtemp())

    def put(self, sid, published, group="", status=VERIFIED, source="Reuters"):
        self.store.add_story({"id": sid, "url": f"https://example.com/{sid}", "title": f"Story {sid}", "source": source, "published": iso(published)}, published)
        self.store.update(sid, published, status=status, shown_at=iso(published), desk="av", group_id=group, summary="A summary.")

    def test_days_are_eastern_and_today_waits_for_the_live_page(self):
        # NOW is 10 a.m. Eastern on Oct. 30. 3 a.m. UTC on the 30th is still the 29th in New York.
        self.put("late29", datetime(2026, 10, 30, 3, 0, tzinfo=timezone.utc))
        self.put("early30", datetime(2026, 10, 30, 5, 0, tzinfo=timezone.utc))
        self.put("dropped", datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc), status=DROPPED)
        written = live_days.write_days(self.store, CONFIG, self.dir, NOW)
        self.assertEqual(written, ["2026-10-29.json", "index.json"])
        day = json.loads((self.dir / "days" / "2026-10-29.json").read_text(encoding="utf-8"))
        self.assertEqual([s["id"] for s in day["stories"]], ["late29"])
        self.assertEqual(json.loads((self.dir / "days" / "index.json").read_text(encoding="utf-8"))["days"], [{"day": "2026-10-29", "stories": 1}])
        written = live_days.write_days(self.store, CONFIG, self.dir, NOW + timedelta(days=1))
        self.assertEqual(written, ["2026-10-30.json", "index.json"])
        index = json.loads((self.dir / "days" / "index.json").read_text(encoding="utf-8"))
        self.assertEqual([d["day"] for d in index["days"]], ["2026-10-30", "2026-10-29"])  # newest first

    def test_an_event_is_one_entry_with_the_other_outlets_listed(self):
        when = datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc)
        self.put("a", when, group="a")
        self.put("b", when + timedelta(minutes=30), group="a", source="DroneLife")
        live_days.write_days(self.store, CONFIG, self.dir, NOW)
        day = json.loads((self.dir / "days" / "2026-10-29.json").read_text(encoding="utf-8"))
        self.assertEqual(len(day["stories"]), 1)
        self.assertEqual([a["source"] for a in day["stories"][0]["also"]], ["DroneLife"])
        self.assertEqual(next(d for d in day["desks"] if d["key"] == "av")["count"], 1)
        self.assertNotIn("score", json.dumps(day))

    def test_recent_days_catch_up_and_older_ones_are_left_alone(self):
        self.put("old", datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc))
        self.put("new", datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc))
        live_days.write_days(self.store, CONFIG, self.dir, NOW)
        self.assertEqual(live_days.write_days(self.store, CONFIG, self.dir, NOW + timedelta(hours=1)), [])  # nothing changed
        self.store.update("old", NOW, status=VERIFIED, summary="Changed long after.")
        self.store.update("new", NOW, summary="Checked later.")
        self.assertEqual(live_days.write_days(self.store, CONFIG, self.dir, NOW + timedelta(hours=2)), ["2026-10-29.json"])
        old = json.loads((self.dir / "days" / "2026-10-20.json").read_text(encoding="utf-8"))
        self.assertEqual(old["stories"][0]["summary"], "A summary.")


class SortTests(unittest.TestCase):
    """One entry per event on the page (Techmeme-style), and U.S. stories apart from world ones (owner, 2026-10-31)."""

    def setUp(self):
        self.store = Store(":memory:")
        self.model = FakeModel("qwen-fake", live_sort={"us": True, "same_as": 0, "reason": "new event"})
        self.desk = Desk(self.store, CONFIG, writer=self.model, verifier=verifier(), fetch=fake_page(NOW), resolve=lambda u: None, clock=lambda: NOW, gpu_busy=lambda: False)
        self.sorter = Sorter(self.desk)

    def put(self, sid, title, source, minutes_ago, status=VERIFIED, summary=""):
        self.store.add_story({"id": sid, "url": f"https://{source.lower().replace(' ', '')}.com/{sid}", "title": title, "source": source,
                              "published": iso(NOW - timedelta(minutes=minutes_ago))}, NOW)
        self.store.update(sid, NOW, status=status, shown_at=iso(NOW - timedelta(minutes=minutes_ago)), desk="drones",
                          summary=summary or f"{title}. The Federal Communications Commission acted on Thursday.")

    def test_the_same_event_from_three_outlets_is_one_entry(self):
        self.put("a", "FCC Opens 800 MHz Spectrum to Drones", "DroneLife", 90)
        self.put("b", "FCC clears drone operations in 800MHz band", "Reuters", 60)
        self.put("c", "Waymo starts robotaxi rides in Denver", "The Verge", 30, summary="Waymo began rides in Denver on Thursday.")
        self.assertEqual(self.sorter.sort_once(), 3)  # newest first: c, then b (asks about a), then a
        self.model.answers["live_sort"] = lambda m: {"us": True, "same_as": 1 if "[1] FCC Opens 800 MHz" in m[1]["content"] else 0, "reason": "same FCC order"}
        for sid in ("a", "b", "c"):
            record = self.store.get(sid)["record"]
            self.store.update(sid, NOW, record={**record, "_sorted": False})
        self.sorter.sort_once()
        self.assertEqual(self.store.get("a")["group_id"], self.store.get("b")["group_id"])
        self.assertEqual(self.store.get("c")["group_id"], "")
        feed = live_feed.build_feed(self.store, CONFIG, now=NOW, level="LIVE")
        ids = [s["id"] for s in feed["stories"]]
        self.assertEqual(len(ids), 2)
        entry = next(s for s in feed["stories"] if s["id"] in ("a", "b"))
        self.assertEqual(entry["id"], "a")  # both checked, same tier: the first report leads the group
        self.assertEqual([x["source"] for x in entry["also"]], ["Reuters"])
        self.assertEqual(entry["members"], ["b"])
        self.assertEqual(sum(1 for t in feed["ticker"] if t["id"] in ("a", "b")), 1)

    def test_a_checked_report_leads_its_group_over_a_quick_one(self):
        self.put("a", "FCC Opens 800 MHz Spectrum to Drones", "DroneLife", 90, status=FIRST_PASS)
        self.put("b", "FCC clears drone operations in 800MHz band", "Reuters", 60)
        for sid in ("a", "b"):
            self.store.update(sid, NOW, group_id="a")
        feed = live_feed.build_feed(self.store, CONFIG, now=NOW, level="LIVE")
        self.assertEqual([s["id"] for s in feed["stories"]], ["b"])

    def test_world_stories_are_marked_and_kept_off_the_wire_bar(self):
        self.put("k", "Russian drone strikes leave Kyiv districts without water", "Kyiv Post", 20)
        self.model.answers["live_sort"] = {"us": False, "same_as": 0, "reason": "war abroad"}
        self.sorter.sort_once()
        feed = live_feed.build_feed(self.store, CONFIG, now=NOW, level="LIVE")
        self.assertEqual(feed["stories"][0]["scope"], "world")
        self.assertEqual(feed["ticker"], [])

    def test_only_reports_sharing_names_within_two_days_are_asked_about(self):
        self.put("a", "FCC Opens 800 MHz Spectrum to Drones", "DroneLife", 60)
        self.put("old", "FCC Opens Spectrum Inquiry", "DroneLife", 60 * 50)
        self.put("x", "Amtrak names new chief", "AP", 30, summary="Amtrak named a chief.")
        pool = self.sorter.pool(NOW)
        story = self.store.get("a")
        self.assertEqual([c["id"] for c in candidates(story, pool)], [])
        self.put("b", "FCC clears drone operations in 800MHz band", "Reuters", 30)
        self.assertEqual([c["id"] for c in candidates(story, self.sorter.pool(NOW))], ["b"])

    def test_sorting_waits_while_the_gpu_is_held_and_gives_up_safely(self):
        self.put("a", "FCC Opens 800 MHz Spectrum to Drones", "DroneLife", 60)
        self.desk.gpu_busy = lambda: True
        self.assertEqual(self.sorter.sort_once(), 0)
        self.assertEqual(self.model.calls, [])
        self.desk.gpu_busy = lambda: False
        self.model.fail = {"live_sort"}
        for _ in range(3):
            self.sorter.sort_once()
        record = self.store.get("a")["record"]
        self.assertTrue(record["_sorted"])
        self.assertEqual(record["_scope"], "us")  # unknown counts as U.S., so a story is never hidden by an outage
