"""The live site's two lanes.

Fast lane (per story, several at once): resolve the link, read the page with the edition's article rules, file it
to a desk, write a first-pass summary that code checks against the article, and put it on the page. Slow lane (in
batches): a second model from another family checks every sentence, the story is scored, and the lead desk decides
whether it takes the lead. A story that keeps failing a step is dropped (or shown as headline only); a story that
crashes a worker twice is quarantined. Nothing that fails a check is ever shown.
"""

from __future__ import annotations

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

import article_extract
import editorial_checks
import grounding
import live_lead
import live_sources
import news_engine
import newsroom_live
import story_groups
import url_resolver
from live_store import DROPPED, FIRST_PASS, HEADLINE, QUARANTINED, QUEUED, VERIFIED, Store, iso, parse_iso, title_overlap

ROOT = Path(__file__).resolve().parent
EDITION_LOCK = Path(os.environ.get("NEWSROOM_EDITION_LOCK") or ROOT / ".runtime" / "edition.lock")  # override when run from a worktree
GPU_PAUSE = "gpu_pause.json"  # in the live runtime folder: {"until": ISO time}; the owner's one-off pause of model work
MIN_WORDS_TO_SUMMARIZE = 25

TRIAGE_SYSTEM = """You file ONE news article for a live transportation-policy news site.
relevant=true only if the ARTICLE is a news report about drones/UAS, counter-UAS, advanced air mobility or eVTOL,
airspace integration, autonomous vehicles or trucks, robotics, supersonic aviation, advanced or high-speed rail,
uncrewed maritime or ground systems, or U.S. transportation policy on those topics. Product reviews, deals,
how-to guides, opinion columns, sports and celebrity items are not relevant. Use only the ARTICLE."""
TRIAGE_CATEGORY = """category: the one reader category that fits best, by key: {choices}. "rail" covers all rail,
freight and passenger; "policy" covers U.S. government action (rules, laws, Congress, agencies) whatever the vehicle."""


def triage_schema(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "relevant": {"type": "boolean"},
            "category": {"type": "string", "enum": [d["key"] for d in config["desks"]]},
            "reason": {"type": "string"},
        },
        "required": ["relevant", "category", "reason"],
    }

SCORE_SYSTEM = """You rate how significant ONE verified news story is for senior U.S. transportation-policy readers.
Use only the ARTICLE. impact, 0-5:
0 = promotional or trivial; 1 = minor company or local news; 2 = notable company, state or city action;
3 = significant national program, major contract, or serious incident; 4 = major federal rule or law, a first
commercial operation of its kind, or a fatal accident with national attention; 5 = historic and rare (a landmark
law, a catastrophic accident, a first that changes the industry).
action, the one class that fits best: final_rule, law, first_commercial_operation, fatal_accident, proposed_rule,
major_award, test, plan, other. reason: under 25 words, naming the fact that drives the rating."""
SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "impact": {"type": "integer", "minimum": 0, "maximum": 5},
        "action": {"type": "string", "enum": list(live_lead.ACTION_CLASSES)},
        "reason": {"type": "string"},
    },
    "required": ["impact", "action", "reason"],
}

# Routine Federal Register housekeeping: never news on its own.
_FR_ROUTINE = re.compile(
    r"information collection|paperwork reduction|sunshine act|privacy act of 1974|system of records|"
    r"agency forms? (?:submitted|undergoing)|meeting of the .* advisory committee|request for nominations",
    re.I,
)
# Paid market-research listings ("... Market Size, Share & 2032 Growth Trends Report") are not news.
_MARKET_REPORT = re.compile(r"market size|\bCAGR\b|growth trends report|market (?:research )?report\b|forecast (?:to|till|through) 20\d\d", re.I)
_OPINION = re.compile(r"/(?:opinion|opinions|editorial|commentary|op-ed)/|^(?:opinion|op-ed|commentary)\s*[:|]", re.I)


def edition_running(lock: Path = EDITION_LOCK) -> bool:
    """True while the 04:30 edition holds its exclusive lock (it needs the whole GPU)."""
    if not lock.exists():
        return False
    try:
        with lock.open("a"):
            return False
    except OSError:
        return True


def gpu_paused(now: datetime | None = None, path: Path | None = None) -> bool:
    """True until the owner's pause file's "until" time passes; an expired, missing or unreadable file is no pause."""
    path = path or Path(os.environ.get("NEWSROOM_LIVE_DIR") or ROOT / ".runtime" / "live_site") / GPU_PAUSE
    try:
        until = parse_iso(str(json.loads(path.read_text(encoding="utf-8")).get("until", "")))
    except (OSError, ValueError, AttributeError):
        return False
    return until is not None and (now or datetime.now(timezone.utc)) < until


def gpu_held() -> bool:
    """The GPU is not ours: the morning edition is running, or the owner paused model work."""
    return edition_running() or gpu_paused()


def desk_for(section: str, config: dict[str, Any]) -> str:
    for desk in config["desks"]:
        if section in desk["sections"]:
            return desk["key"]
    return config["desks"][-1]["key"]


_DRONE_WORDS = re.compile(r"\b(?:drones?|uas|uavs?|unmanned|uncrewed|quadcopter|bvlos|remote id|airspace)\b", re.I)
_RAIL_WORDS = re.compile(r"\b(?:rail(?:way|road)?s?|trains?|locomotives?|rolling stock|amtrak|metro|subway|tram)\b", re.I)


def file_story(section: str, text: str, config: dict[str, Any]) -> str:
    """The category before any model sees the story. The edition's infer_section ends in "UAS / Drones" for
    anything it does not recognize, so a story with no drone words is filed by rail words, else in the last
    category, never as a drone story (the triage model's category replaces this when it runs)."""
    if section == "UAS / Drones" and not _DRONE_WORDS.search(text):
        has_rail = any(d["key"] == "rail" for d in config["desks"])
        return "rail" if has_rail and _RAIL_WORDS.search(text) else config["desks"][-1]["key"]
    return desk_for(section, config)


def _agency_headline(record: dict[str, Any]) -> dict[str, Any]:
    """A refused agency release as a headline-only record: the search's title without its " | Agency" tail."""
    title = str(record.get("title", ""))
    if " | " in title:
        title = title.rsplit(" | ", 1)[0]
    return {**record, "title": title.strip(), "page_text": "", "_agency_refused": True,
            "date_source": "date of the news search that found it (the agency's site refuses our reader)"}


def date_only(value: datetime | None) -> bool:
    """article_extract.parse_date stamps a bare date at noon Eastern; that exact noon means no time was given."""
    if value is None:
        return False
    local = value.astimezone(article_extract.EASTERN)
    return (local.hour, local.minute, local.second, local.microsecond) == (12, 0, 0, 0)


def settle_published(page: datetime | None, feed: datetime | None, found: datetime | None) -> tuple[datetime | None, bool]:
    """The publication time to show and sort by, and whether only the day is known. A page that gives only a
    date never replaces a feed time from that same day, and no story is stamped later than we found it (a bare
    date for today would otherwise sit at noon and float above stories that came in after it)."""
    when, day_only = page or feed, date_only(page or feed)
    if day_only and feed is not None and not date_only(feed):
        eastern = article_extract.EASTERN
        if feed.astimezone(eastern).date() == when.astimezone(eastern).date():
            when, day_only = feed, False
    if when is not None and found is not None and when > found:
        when = found
    return when, day_only


class StepFailed(Exception):
    """A step failed in an expected way (bad page, model refused, budget exceeded); counts as an attempt."""


class ModelDown(Exception):
    """The writer could not be reached or timed out. Not the story's fault: it stays queued, costs no attempt, and
    the doctor's HEADLINES level shows it as headline and link if the writer stays down."""


@dataclass
class Health:
    """What the doctor reads: consecutive failures per model role and when each last worked."""
    failures: dict[str, int] = field(default_factory=lambda: {"writer": 0, "verifier": 0, "scorer": 0})
    last_ok: dict[str, str] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def ok(self, role: str) -> None:
        with self.lock:
            self.failures[role] = 0
            self.last_ok[role] = iso(datetime.now(timezone.utc))

    def fail(self, role: str) -> None:
        with self.lock:
            self.failures[role] = self.failures.get(role, 0) + 1


class Desk:
    def __init__(
        self,
        store: Store,
        config: dict[str, Any],
        *,
        writer: Any,
        verifier: Any,
        scorer: Any | None = None,
        fetch: Callable[[str], tuple[int | None, str, str]] | None = None,
        resolve: Callable[[str], str | None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        gpu_busy: Callable[[], bool] = gpu_held,
        health: Health | None = None,
    ) -> None:
        self.store = store
        self.config = config
        self.writer = writer
        self.verifier = verifier
        self.scorer = scorer or writer
        self.clock = clock
        self.gpu_busy = gpu_busy
        self.health = health or Health()
        self.budgets = config["desk"]["budgets_seconds"]
        session = requests.Session()
        self.fetch = fetch or (lambda url: article_extract.fetch_page(url, session, float(self.budgets["read"])))
        self.resolve = resolve or (lambda url: url_resolver.resolve_url(url, session))
        self.write = grounding.make_writer(writer)
        self.model_slots = threading.BoundedSemaphore(int(config["desk"]["parallel_stories"]))
        self.level = "LIVE"  # set by the doctor; HEADLINES skips the writer, LITE skips the slow lane

    # --- fast lane --------------------------------------------------------------------------------------------
    def first_pass(self, story_id: str) -> str:
        """Move one queued story as far as it can go now. Returns its new status. Never raises."""
        story = self.store.get(story_id)
        if story is None or story["status"] != QUEUED:
            return story["status"] if story else DROPPED
        now = self.clock()
        try:
            return self._first_pass(story, now)
        except StepFailed as exc:
            return self._attempt_failed(story, now, str(exc))
        except ModelDown as exc:
            self.store.update(story["id"], now, error=str(exc)[:400])
            return QUEUED
        except Exception as exc:  # noqa: BLE001 - a crash costs this story, never the worker
            crashes = story["crashes"] + 1
            if crashes >= 2:
                self.store.update(story["id"], now, status=QUARANTINED, crashes=crashes, error=f"crashed twice: {exc!r}"[:400])
                newsroom_live.emit("live-quarantine", desk="doctor", title=story["title"][:160], error=repr(exc)[:200])
                return QUARANTINED
            self.store.update(story["id"], now, crashes=crashes, error=repr(exc)[:400])
            return QUEUED

    def _attempt_failed(self, story: dict[str, Any], now: datetime, reason: str) -> str:
        attempts = story["stage_attempts"] + 1
        if attempts >= 2:
            self.store.update(story["id"], now, status=DROPPED, stage_attempts=attempts, error=reason[:400])
            newsroom_live.emit("live-drop", desk="editors", title=story["title"][:160], reason=reason[:200])
            return DROPPED
        self.store.update(story["id"], now, stage_attempts=attempts, error=reason[:400])
        return QUEUED

    def _agency_refuses(self, record: dict[str, Any], page: tuple[int | None, str, str] | None, now: datetime) -> bool:
        """An agency site that refuses our reader (config desk.headline_only_pages; owner, 2026-10-30) still gets its
        release on the page as a headline and link, dated by the news search that found it, if that date is recent."""
        parts = urlparse(str(record.get("url", "")))
        page_path = parts.netloc.casefold().removeprefix("www.") + parts.path
        if not any(page_path.startswith(prefix) for prefix in self.config["desk"].get("headline_only_pages") or []):
            return False  # only the agencies' press-release pages, not their notices and charts
        if page is None or page[0] not in (401, 403, article_extract.ROBOTS_DISALLOWED):
            return False  # it answered (or the page is gone), so the normal checks stand; a 403's body is its error page
        published = parse_iso(str(record.get("published", "")))
        return published is not None and article_extract.window_cutoff(now) <= published <= now + timedelta(minutes=5)

    def _drop(self, story: dict[str, Any], now: datetime, reason: str) -> str:
        self.store.update(story["id"], now, status=DROPPED, error=reason[:400])
        newsroom_live.emit("live-drop", desk="editors", title=story["title"][:160], reason=reason[:200])
        return DROPPED

    def _first_pass(self, story: dict[str, Any], now: datetime) -> str:
        record = dict(story["record"])
        enriched = story["record"].get("_enriched")
        if not enriched:
            url = str(record.get("url", ""))
            if url_resolver.is_google_news_url(url):
                resolved = self.resolve(url)
                if not resolved:
                    raise StepFailed("Google News link could not be resolved to the publisher")
                record["url"] = resolved
                if live_sources.denied_host(resolved, self.config):
                    return self._drop(story, now, "social or market-research site")
                if not self.store.mark_seen(resolved, now):
                    return self._drop(story, now, "the publisher's page was already found by another source")
            if _MARKET_REPORT.search(str(record.get("title", ""))):
                return self._drop(story, now, "market-research listing, not news")
            official_api = record.get("origin") == "Federal Register API"
            if official_api and _FR_ROUTINE.search(str(record.get("title", ""))):
                return self._drop(story, now, "routine Federal Register notice")
            page = None if official_api else self.fetch(str(record["url"]))
            verdict = article_extract.evaluate_candidate(
                record, page, now=now, follow=self.fetch,
            )
            refused = not verdict.keep and self._agency_refuses(record, page, now)
            if not verdict.keep and not refused:
                # Hard rules (stale, not an article, bad headline) do not get better on a retry.
                return self._drop(story, now, verdict.reason)
            enriched = verdict.record if verdict.keep else _agency_headline(record)
            reason = editorial_checks.off_topic_reason(enriched)
            if reason:
                return self._drop(story, now, reason)
            twin = self.store.similar_visible(enriched["title"], now - timedelta(hours=48), float(self.config["desk"]["similar_title_overlap"]))
            if twin is not None:
                self._add_pickup(twin, enriched, now)
                return self._drop(story, now, f"same report as {twin['source']}: {twin['title'][:80]}")
            if official_api:
                # The API gives no page to read; the abstract is the record's own text.
                enriched["page_text"] = str(record.get("summary", ""))
                enriched["source"] = "Federal Register"
            section = news_engine.infer_section(enriched)
            published, day_only = settle_published(
                parse_iso(str(enriched.get("published", ""))), parse_iso(story["published"]), parse_iso(story["found_at"]))
            self.store.update(
                story["id"], now,
                url=str(enriched["url"]), title=str(enriched["title"]),
                source=editorial_checks.source_label(str(enriched.get("source", "")), str(enriched["url"]), str(enriched.get("page_original_publisher", ""))),
                published=iso(published) if published else story["published"],
                section=section, desk=file_story(section, f"{enriched['title']} {enriched.get('page_text', '')[:3000]}", self.config),
                official=int(bool(record.get("official_source")) or story_groups.source_tier(enriched) == 1),
                page_text=str(enriched.get("page_text", "")),
                record={**{k: v for k, v in enriched.items() if k != "page_text"}, "_enriched": True, "_date_only": day_only},
            )
            story = self.store.get(story["id"]) or story

        if story["record"].get("_agency_refused"):
            # Nothing was read, so nothing is summarized: the agency's own headline and link only. The writer still
            # judges from the headline whether it belongs in the portfolio, as it does for every story.
            if self.level == "HEADLINES" or self.gpu_busy():
                return QUEUED
            with self.model_slots:
                relevant, why, _ = self._triage(story["title"], story["title"])
            if not relevant:
                return self._drop(story, now, f"not a portfolio story: {why}")
            return self._show(story, now, HEADLINE, "", "headline only: the agency's site refuses our reader")
        if self.level == "HEADLINES" or self.gpu_busy():
            waited = now - (parse_iso(story["found_at"]) or now)
            if waited >= timedelta(minutes=float(self.config["desk"]["first_pass_wait"])) or self.level == "HEADLINES":
                return self._show(story, now, HEADLINE, "", "headline only: the writer was busy or down")
            return QUEUED  # read and filed; the writer picks it up when the GPU is free

        source = story["page_text"]
        short = len(source.split()) < MIN_WORDS_TO_SUMMARIZE
        with self.model_slots:
            relevant, why, category = self._triage(story["title"], source or str(story["record"].get("summary", "")))
            if not relevant:
                return self._drop(story, now, f"not a portfolio story: {why}")
            if category and category != story["desk"]:
                self.store.update(story["id"], now, desk=category)
            if short:
                return self._show(story, now, HEADLINE, "", "headline only: the article text is too short to summarize")
            summary, method = self._write_checked(story["title"], source)
        if not summary:
            return self._show(story, now, HEADLINE, "", "headline only: no summary passed the article check")
        return self._show(story, now, FIRST_PASS, summary, method)

    def _triage(self, title: str, source: str) -> tuple[bool, str, str]:
        """(relevant, reason, category key or '')."""
        desks = self.config["desks"]
        system = TRIAGE_SYSTEM + "\n" + TRIAGE_CATEGORY.format(choices="; ".join(f'"{d["key"]}" = {d["name"]}' for d in desks))
        try:
            result = self.writer.generate_structured(
                [{"role": "system", "content": system},
                 {"role": "user", "content": f"HEADLINE\n{title}\n\nARTICLE\n{source[:3000]}"}],
                schema_name="live_triage", schema=triage_schema(self.config), max_output_tokens=160,
            )
        except Exception as exc:  # noqa: BLE001
            self.health.fail("writer")
            raise ModelDown(f"writer unavailable: {exc}") from exc
        self.health.ok("writer")
        category = str(result.value.get("category", ""))
        valid = {d["key"] for d in desks}
        return bool(result.value.get("relevant")), str(result.value.get("reason", ""))[:200], category if category in valid else ""

    def _write_checked(self, title: str, source: str) -> tuple[str, str]:
        """A first-pass summary every name, number and date of which is in the article ('' when none passes)."""
        problems = ""
        for attempt in range(2):
            try:
                text = self.write(title, source, problems)
            except Exception as exc:  # noqa: BLE001
                self.health.fail("writer")
                raise ModelDown(f"writer unavailable: {exc}") from exc
            self.health.ok("writer")
            text = grounding.drop_filler_sentences(editorial_checks.style_text(text), source)
            found = grounding.deterministic_problems(text, source, title) if text else [grounding.Problem("empty", "empty summary")]
            if not found:
                return text, "first-pass" if attempt == 0 else "first-pass-rewritten"
            problems = "; ".join(str(p) for p in found)[:500]
        return "", ""

    def _show(self, story: dict[str, Any], now: datetime, status: str, summary: str, method: str) -> str:
        self.store.update(story["id"], now, status=status, summary=summary, summary_method=method, shown_at=iso(now), stage_attempts=0, error="")
        self._bump_source(story, "shown")
        newsroom_live.emit("live-arrive", desk="layout", status=status, desk_key=story.get("desk", ""), source=story.get("source", ""), title=story["title"][:160])
        return status

    def _add_pickup(self, twin: dict[str, Any], record: dict[str, Any], now: datetime) -> None:
        source = editorial_checks.source_label(str(record.get("source", "")), str(record.get("url", "")))
        also = [a for a in twin["also"] if a.get("url") != record.get("url")]
        if source.casefold() != twin["source"].casefold() and all(a.get("source", "").casefold() != source.casefold() for a in also):
            also.append({"source": source, "url": str(record.get("url", ""))})
        self.store.update(twin["id"], now, also=also[: story_groups.MAX_ALSO_COVERED])

    def _bump_source(self, story: dict[str, Any], column: str) -> None:
        name = str(story["record"].get("wire_source", ""))
        state = self.store.source(name) if name else None
        if state:
            self.store.update_source(name, **{column: int(state.get(column, 0)) + 1})

    # --- slow lane --------------------------------------------------------------------------------------------
    def verify(self, story_id: str) -> str:
        """Second-model check of a first-pass story, then score it and offer it to the lead desk. Never raises."""
        story = self.store.get(story_id)
        if story is None or story["status"] != FIRST_PASS:
            return story["status"] if story else DROPPED
        now = self.clock()
        try:
            status = self._verify(story, now)
        except StepFailed as exc:
            attempts = story["stage_attempts"] + 1
            if attempts >= 2:
                # Two failed checks: the reader keeps the headline and link, never an unchecked sentence.
                self.store.update(story["id"], now, status=HEADLINE, summary="", summary_method="verify-failed", stage_attempts=attempts, error=str(exc)[:400])
                return HEADLINE
            self.store.update(story["id"], now, stage_attempts=attempts, error=str(exc)[:400])
            return FIRST_PASS
        except Exception as exc:  # noqa: BLE001
            crashes = story["crashes"] + 1
            self.store.update(story["id"], now, crashes=crashes, error=repr(exc)[:400],
                              **({"status": HEADLINE, "summary": "", "summary_method": "verify-crashed"} if crashes >= 2 else {}))
            return HEADLINE if crashes >= 2 else FIRST_PASS
        if status == VERIFIED:
            self.offer_lead(story_id)
        return status

    def _verify(self, story: dict[str, Any], now: datetime) -> str:
        verifier_error: list[str] = []
        entail_with = grounding.make_entailment_check(self.verifier)

        def entail(source: str, sentences: list[str]) -> list[tuple[bool, str]]:
            try:
                return entail_with(source, sentences)
            except Exception as exc:  # noqa: BLE001 - ground_summary would treat this as "no objection"; we do not
                verifier_error.append(str(exc))
                raise

        summary, record = grounding.ground_summary(story["title"], story["page_text"], write=self.write, entail=entail, draft=story["summary"])
        if verifier_error:
            self.health.fail("verifier")
            raise StepFailed(f"verifier unavailable: {verifier_error[-1][:200]}")
        self.health.ok("verifier")
        if record.method not in ("model", "regenerated") or not summary:
            self.store.update(story["id"], now, status=HEADLINE, summary="", summary_method=f"verify:{record.method}", error="; ".join(record.failures)[:400])
            return HEADLINE
        score = self._score(story, summary, now)
        notes = story["notes"]
        if summary != story["summary"]:
            notes = notes + [{"t": iso(now), "note": "summary rewritten after the second check"}]
        self.store.update(
            story["id"], now, status=VERIFIED, summary=summary, summary_method=f"verified:{record.method}",
            score=score.total, score_parts=score.parts, notes=notes, stage_attempts=0, error="",
        )
        newsroom_live.emit("live-verified", desk="standards", title=story["title"][:160], score=score.total)
        return VERIFIED

    def _score(self, story: dict[str, Any], summary: str, now: datetime) -> live_lead.Score:
        try:
            result = self.scorer.generate_structured(
                [{"role": "system", "content": SCORE_SYSTEM},
                 {"role": "user", "content": f"HEADLINE\n{story['title']}\n\nSUMMARY\n{summary}\n\nARTICLE\n{story['page_text'][:5000]}"}],
                schema_name="live_score", schema=SCORE_SCHEMA, max_output_tokens=1500,
            )
            impact, action = int(result.value["impact"]), str(result.value["action"])
            self.health.ok("scorer")
        except Exception as exc:  # noqa: BLE001
            self.health.fail("scorer")
            raise StepFailed(f"scorer unavailable: {exc}") from exc
        record = story["record"]
        published = parse_iso(story["published"])
        penalties = []
        if published and now - published > timedelta(hours=24):
            penalties.append("older_than_24h")
        if _OPINION.search(urlparse(story["url"]).path) or _OPINION.search(story["title"]):
            penalties.append("opinion")
        outlets = {a.get("source", "").casefold() for a in story["also"]} - {story["source"].casefold(), ""}
        if not outlets and story_groups.source_tier({"url": story["url"]}) == 3:
            penalties.append("single_weak_source")
        international = news_engine.record_is_international(record)
        week = self.store.visible(now - timedelta(days=7), limit=600)
        novel = not any(
            other["id"] != story["id"] and title_overlap(other["title"], story["title"]) >= 0.6
            and (parse_iso(other["published"]) or now) < (published or now)
            for other in week
        )
        return live_lead.significance(
            impact=impact, action=action, pickup_outlets=len(outlets), official=bool(story["official"]),
            us_relevance=3 if international else 10, novel=novel, penalties=penalties,
        )

    # --- lead desk --------------------------------------------------------------------------------------------
    # The owner can force any story on the page to lead for 1-48 hours (live_override reads the request). While
    # that holds, no story replaces it; when it ends, the best checked story of the last day that scores enough
    # leads, or no story does.
    def override(self, now: datetime | None = None) -> dict[str, Any] | None:
        held = self.store.get_state(live_lead.OVERRIDE_KEY)
        if not held:
            return None
        until = parse_iso(str(held.get("until", "")))
        return held if until and (now or self.clock()) < until else None

    def force_lead(self, story_id: str, hours: int, note: str = "") -> str:
        """Make a story the lead for `hours` (1-48); 0 ends the owner's choice now. Returns what was done."""
        now = self.clock()
        if hours == 0:
            if self.override(now) is None:
                return "no headline override was running"
            self.store.set_state(live_lead.OVERRIDE_KEY, None)
            self._lead_after_override(now, "the owner ended the override")
            return "override ended"
        if not 1 <= hours <= live_lead.OVERRIDE_MAX_HOURS:
            raise ValueError(f"hours must be 1 to {live_lead.OVERRIDE_MAX_HOURS}, not {hours}")
        story = self.store.get(story_id)
        if story is None or story["status"] not in (FIRST_PASS, VERIFIED, HEADLINE):
            raise ValueError(f"no story {story_id!r} is on the page")
        until = now + timedelta(hours=hours)
        self.store.set_state(live_lead.OVERRIDE_KEY, {"story_id": story_id, "until": iso(until), "set_at": iso(now), "hours": hours, "note": note[:200]})
        self.store.set_lead(story_id, now, int(story["score"] or 0), None, f"owner's choice for {hours} h {note}".strip())
        newsroom_live.emit("live-lead-override", desk="editors", title=story["title"][:160], hours=hours, until=iso(until))
        return f"{story['title'][:120]} leads until {iso(until)}"

    def _lead_after_override(self, now: datetime, reason: str) -> None:
        pool = [s for s in self.store.visible(now - live_lead.BEST_OF_WINDOW) if s["status"] == VERIFIED and (s["score"] or 0) >= live_lead.FLOOR]
        best = max(pool, key=lambda s: (s["score"] or 0, s["published"]), default=None)
        if best is None:
            self.store.set_lead("", now, 0, None, f"{reason}; no story of the last day scores enough to lead")
        else:
            self.store.set_lead(best["id"], now, int(best["score"] or 0), None, f"{reason}; best checked story of the last day")

    def offer_lead(self, story_id: str) -> bool:
        story = self.store.get(story_id)
        if story is None or story["status"] != VERIFIED:
            return False
        now = self.clock()
        if self.override(now) is not None:
            return False
        lead = self.store.current_lead()
        candidate = {"story_id": story["id"], "score": story["score"], "verified": True, "published": parse_iso(story["published"])}
        decision = live_lead.decide(lead, candidate, now)
        newsroom_live.emit("live-lead-check", desk="editors", title=story["title"][:160], score=story["score"], swap=decision.swap, reason=decision.reason)
        if decision.swap:
            self.store.set_lead(story["id"], now, int(story["score"] or 0), decision.bar, decision.reason)
        return decision.swap

    def check_stale_lead(self) -> bool:
        now = self.clock()
        held = self.store.get_state(live_lead.OVERRIDE_KEY)
        if held:
            if self.override(now) is not None:
                return False
            self.store.set_state(live_lead.OVERRIDE_KEY, None)
            self._lead_after_override(now, f"the owner's {held.get('hours')} h override ended")
            return True
        lead = self.store.current_lead()
        pool = [
            {"story_id": s["id"], "score": s["score"], "verified": True, "published": parse_iso(s["published"])}
            for s in self.store.visible(now - live_lead.BEST_OF_WINDOW) if s["status"] == VERIFIED
        ]
        best = live_lead.stale_takeover(lead, pool, now)
        if best is None:
            if lead and lead["story_id"] and now - lead["set_at"] >= live_lead.STALE_LEAD:
                self.store.set_lead("", now, 0, None, "the lead stood 30 hours and no story of the last day scores enough to lead")
                return True
            return False
        self.store.set_lead(best["story_id"], now, int(best["score"] or 0), None, "the lead stood 30 hours; best verified story of the last day")
        return True

    # --- loops ------------------------------------------------------------------------------------------------
    def run_fast(self, stop: threading.Event, beat: Callable[[], None] = lambda: None) -> None:
        workers = max(2, int(self.config["desk"]["parallel_stories"]) * 2)  # reading is network-bound; models are gated
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="live-fast") as pool:
            while not stop.is_set():
                beat()
                batch = self.store.by_status(QUEUED, limit=workers * 2)
                if not batch:
                    stop.wait(3)
                    continue
                list(pool.map(lambda s: self.first_pass(s["id"]), batch))

    def run_slow(self, stop: threading.Event, beat: Callable[[], None] = lambda: None) -> None:
        every = timedelta(minutes=float(self.config["desk"]["verify_every"]))
        size = int(self.config["desk"]["verify_batch"])
        last = datetime.min.replace(tzinfo=timezone.utc)
        while not stop.is_set():
            beat()
            waiting = self.store.by_status(FIRST_PASS, limit=size)
            now = self.clock()
            if waiting and self.level == "LIVE" and not self.gpu_busy() and (len(waiting) >= size or now - last >= every):
                last = now
                for story in waiting:
                    if stop.is_set():
                        break
                    beat()
                    self.verify(story["id"])
            self.check_stale_lead()
            stop.wait(10)

