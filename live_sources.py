"""The wire for the live site: each source on its own clock, with conditional GETs and a circuit breaker.

Sources: agency feeds (every 5 min), agency site searches for the agencies whose feeds refuse us (Google News
limited to faa.gov, transportation.gov and whitehouse.gov, every 10), trade feeds (every 10), the Federal Register
API (every 15) and Google News queries (one every 30 s, so each query comes round about every 36 min). Every
feed entry is handed to the store once; the store's `seen` table makes repeat sightings free.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import feedparser
import requests
import yaml

import live_posts
import news_engine
import newsroom_live
import web_discovery
from live_store import Store, iso, parse_iso

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "live.yaml"
HEADERS = {"User-Agent": "AdvancedTransportationNewsBot/1.0 (+https://advancedtransportation.live)"}


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@dataclass
class Source:
    name: str
    kind: str  # "rss" | "google" | "federal_register" | "searxng"
    every: timedelta
    url: str = ""
    query: str = ""
    section: str = ""
    official: bool = False
    site: str = ""  # Google sources only: limit the search to this agency site
    posts: bool = False  # feeds only the "From official sources" strip (live_posts), never the story queue
    topical: bool = True  # strip items must be on the site's topics (off for agencies that only post on them)


def build_sources(config: dict[str, Any]) -> list[Source]:
    wire = config["wire"]
    sources = [
        Source(f"Agency: {name}", "rss", timedelta(minutes=wire["official_every"]), url=url, official=True)
        for name, url in web_discovery.OFFICIAL_FEEDS.items()
    ]
    sources += [
        Source(f"Agency: {name}", "google", timedelta(minutes=wire["agency_search_every"]), site=site, official=True)
        for name, site in web_discovery.OFFICIAL_SITE_SEARCHES.items()
    ]
    sources += [
        Source(f"Trade: {name}", "rss", timedelta(minutes=wire["trade_every"]), url=url)
        for name, url in wire["trade_feeds"].items()
    ]
    sources.append(Source("Federal Register", "federal_register", timedelta(minutes=wire["federal_register_every"]), official=True))
    if wire.get("searxng_every"):
        sources.append(Source("Web search", "searxng", timedelta(minutes=wire["searxng_every"])))
    posts = config.get("posts") or {}
    for spec in posts.get("sources", []):
        kind = str(spec["kind"])
        sources.append(Source(
            f"Official: {spec['name']}", kind, timedelta(minutes=float(posts.get("every_minutes", 60))),
            url=str(spec.get("url", "")), site=str(spec.get("site", "")), query=str(spec.get("handle", "")),
            official=True, posts=True, topical=bool(spec.get("topical", True)),
        ))
    queries = [(section, q) for section, qs in news_engine.NEWS_QUERIES.items() for q in qs]
    google_cycle = timedelta(seconds=max(1, len(queries)) * wire["google_min_gap_seconds"])
    sources += [Source(f"Google News: {q}", "google", google_cycle, query=q, section=s) for s, q in queries]
    return sources


def denied_host(url: str, config: dict[str, Any]) -> bool:
    """Social and market-research hosts never reach the page (checked on the wire and again after a Google link
    resolves to its publisher)."""
    host = urlparse(url).netloc.casefold().removeprefix("www.")
    return any(host == h or host.endswith("." + h) for h in config["wire"].get("deny_hosts", []))


def rest_for(failures: int, config: dict[str, Any]) -> timedelta:
    """How long a source rests after `failures` failures in a row (none before the threshold)."""
    wire = config["wire"]
    over = failures - int(wire["rest_after_failures"])
    if over < 0:
        return timedelta(0)
    steps = wire["rest_minutes"]
    return timedelta(minutes=steps[min(over, len(steps) - 1)])


def fetch_rss(source: Source, store: Store, since: datetime, session: requests.Session) -> list[dict[str, Any]]:
    """One feed, with If-None-Match / If-Modified-Since so an unchanged feed costs one tiny response."""
    state = store.source(source.name) or {}
    headers = dict(HEADERS)
    if state.get("etag"):
        headers["If-None-Match"] = state["etag"]
    if state.get("modified"):
        headers["If-Modified-Since"] = state["modified"]
    response = session.get(source.url, headers=headers, timeout=20)
    if response.status_code == 304:
        return []
    response.raise_for_status()
    store.update_source(source.name, etag=response.headers.get("ETag", ""), modified=response.headers.get("Last-Modified", ""))
    feed = feedparser.parse(response.content)
    if getattr(feed, "bozo", False) and not feed.entries:
        raise ValueError("feed could not be parsed")
    label = source.name.split(": ", 1)[-1]
    items = []
    for entry in feed.entries[:60]:
        published = web_discovery._published(str(entry.get("published") or entry.get("updated") or ""), datetime.now(timezone.utc))
        if published < since:
            continue
        title = web_discovery.clean_spaces(str(entry.get("title", "")))
        url = web_discovery.normalize_url(str(entry.get("link", "")))
        if not title or not url:
            continue
        items.append({
            "id": web_discovery.stable_id("live", url),
            "title": title,
            "summary": web_discovery.clean_spaces(news_engine.strip_html(str(entry.get("summary", ""))))[:700],
            "source": label,
            "url": url,
            "published": published.isoformat(),
            "origin": "Official source RSS" if source.official else "Trade RSS",
        })
    return items


class Wire:
    """Polls whichever sources are due, one at a time per kind, and queues new stories in the store."""

    def __init__(
        self,
        store: Store,
        config: dict[str, Any],
        *,
        sources: list[Source] | None = None,
        fetchers: dict[str, Callable[[Source, datetime], list[dict[str, Any]]]] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.store = store
        self.config = config
        self.sources = sources if sources is not None else build_sources(config)
        self.clock = clock
        self.session = requests.Session()
        self.fetchers = fetchers or {
            "rss": lambda s, since: fetch_rss(s, store, since, self.session),
            "google": self._google,
            "federal_register": lambda s, since: _ok(news_engine.fetch_federal_register(since, self.clock())),
            "searxng": lambda s, since: _ok(web_discovery.fetch_searxng(os.environ.get("SEARXNG_BASE_URL", "http://localhost:8080"), since, self.clock())),
            "bluesky": lambda s, since: live_posts.fetch_bluesky(s.query, s.name.split(": ", 1)[-1], since, self.session),
        }
        self.google_gap = float(config["wire"]["google_min_gap_seconds"])
        self._last_google = 0.0
        now = clock()
        # Stagger first polls so the page is busy from the first minute instead of all at once.
        for index, source in enumerate(self.sources):
            store.ensure_source(source.name, now + timedelta(seconds=(index * 7) % max(60, int(source.every.total_seconds()))))

    def _google(self, source: Source, since: datetime) -> list[dict[str, Any]]:
        if source.site:
            name = source.name.split(": ", 1)[-1]
            items, error = news_engine.fetch_official_site_search(name, source.site, since, self.clock(), max_items=25)
        else:
            items, error = news_engine.fetch_google_news(source.section, source.query, since, self.clock(), max_items=25)
        if error:
            if any(code in error for code in ("429", "503")):
                self.google_gap = min(self.google_gap * 2, float(self.config["wire"]["google_max_gap_seconds"]))
            raise RuntimeError(error)
        self.google_gap = max(float(self.config["wire"]["google_min_gap_seconds"]), self.google_gap * 0.9)
        return items

    def denied(self, url: str) -> bool:
        return denied_host(url, self.config)

    def due(self) -> list[Source]:
        now = self.clock()
        out = []
        for source in self.sources:
            state = self.store.source(source.name) or {}
            rest = parse_iso(state.get("rest_until", "")) if state.get("rest_until") else None
            if rest and rest > now:
                continue
            next_due = parse_iso(state.get("next_due", "")) or now
            if next_due <= now:
                out.append((next_due, source))
        # Longest-waiting first, so every Google search gets its turn (by name, the strip's searches never did).
        return [s for _, s in sorted(out, key=lambda pair: (pair[1].kind == "google", pair[0], pair[1].name))]

    def poll(self, source: Source) -> int:
        """Poll one source; returns the number of new stories queued. Never raises."""
        now = self.clock()
        state = self.store.source(source.name) or {}
        keep_days = float((self.config.get("posts") or {}).get("keep_days", 7))
        since = now - (timedelta(days=keep_days) if source.posts else timedelta(hours=float(self.config["wire"]["window_hours"])))
        try:
            items = self.fetchers[source.kind](source, since)
        except Exception as exc:  # noqa: BLE001 - one source failing must never stop the wire
            failures = int(state.get("failures", 0)) + 1
            rest = rest_for(failures, self.config)
            self.store.update_source(
                source.name, failures=failures, last_error=str(exc)[:300], last_poll=iso(now),
                next_due=iso(now + source.every), rest_until=iso(now + rest) if rest else "",
                polls=int(state.get("polls", 0)) + 1,
            )
            newsroom_live.emit("live-source-fail", desk="wire", source=source.name, failures=failures, rest_minutes=rest.total_seconds() / 60, error=str(exc)[:200])
            return 0
        if source.official:
            self._to_strip(source, items, now, keep_days)
        if source.posts:
            self.store.update_source(source.name, failures=0, rest_until="", last_error="", last_poll=iso(now),
                                     next_due=iso(now + source.every), polls=int(state.get("polls", 0)) + 1)
            return 0
        new = 0
        for item in items:
            if not item.get("url") or not item.get("title"):
                continue
            if not news_engine.automated_record_is_portfolio_relevant(item) or self.denied(str(item["url"])):
                continue
            if not self.store.mark_seen(str(item["url"]), now):
                continue
            self.store.add_story({**item, "wire_source": source.name, "official_source": source.official}, now)
            new += 1
        self.store.update_source(
            source.name, failures=0, rest_until="", last_error="", last_poll=iso(now), next_due=iso(now + source.every),
            polls=int(state.get("polls", 0)) + 1, found=int(state.get("found", 0)) + new,
        )
        newsroom_live.emit("live-sweep", desk="wire", source=source.name, items=len(items), new=new)
        return new

    def _to_strip(self, source: Source, items: list[dict[str, Any]], now: datetime, keep_days: float) -> None:
        """Official sources also feed the "From official sources" strip (title, link and time only); Federal
        Register documents are notices, not releases, and stay off it."""
        if source.kind not in ("rss", "google", "bluesky"):
            return
        org = source.name.split(": ", 1)[-1]
        if source.kind == "bluesky":
            strip = items
        else:
            label = "Press release" if source.kind == "rss" else "On its website"
            strip = live_posts.from_wire_items(org, [i for i in items if not self.denied(str(i.get("url", "")))], label)
        if source.topical:
            strip = live_posts.relevant(strip)
        live_posts.record(self.store, strip, now, keep_days)

    def google_turn(self) -> bool:
        """At most one Google News query per gap (the gap doubles while Google is throttling us)."""
        if time.monotonic() - self._last_google < self.google_gap:
            return False
        self._last_google = time.monotonic()
        return True

    def run_once(self) -> int:
        """Poll every due non-Google source and at most one Google query, one after another; returns stories queued."""
        total = 0
        for source in self.due():
            if source.kind == "google" and not self.google_turn():
                continue
            total += self.poll(source)
        return total

    def run(self, stop: threading.Event, beat: Callable[[], None] = lambda: None) -> None:
        """Polls run in a small pool, so a slow source (the Federal Register makes 18 requests) never holds up the rest."""
        inflight: dict[str, Future] = {}
        google = {s.name for s in self.sources if s.kind == "google"}
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="live-wire") as pool:
            while not stop.is_set():
                beat()
                for name in [n for n, f in inflight.items() if f.done()]:
                    inflight.pop(name)
                google_busy = any(n in google for n in inflight)
                for source in self.due():
                    if source.name in inflight:
                        continue
                    if source.kind == "google":
                        if google_busy or not self.google_turn():
                            continue
                        google_busy = True
                    inflight[source.name] = pool.submit(self.poll, source)
                stop.wait(2)

def _ok(result: tuple[list[dict[str, Any]], Any]) -> list[dict[str, Any]]:
    """Adapt the edition's (items, errors) fetchers: errors with no items count as a failed poll."""
    items, errors = result
    if errors and not items:
        raise RuntimeError("; ".join(errors)[:300] if isinstance(errors, list) else str(errors))
    return items
