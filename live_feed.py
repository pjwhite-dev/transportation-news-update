"""Render the live site's feed.json from the store, and copy the reader page next to it.

The page (site_assets/live/) is static; it polls feed.json and animates whatever changed. Only what a reader
needs leaves the store: no article text, no model reasons, and no significance score (owner has not decided
whether to show it; decisions.md 2026-10-30). The public labels never say "verified": the checks are on our
summary against the article, not on the reporting itself (owner, 2026-10-30).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import live_lead
import live_posts
import live_sort
from live_store import FIRST_PASS, HEADLINE, VERIFIED, Store, iso, parse_iso, title_key, title_overlap

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "site_assets" / "live"
LOGIN = ROOT / "site_assets" / "login"  # the owner's override page (advancedtransportation.live/login/)
DAYS = ROOT / "site_assets" / "days"  # the daily archive page (advancedtransportation.live/days/; live_days.py)
BUBBLES = ROOT / "site_assets" / "bubbles"  # the headlines as drifting bubbles (advancedtransportation.live/bubbles/)
FEED_VERSION = 1
BADGES = {FIRST_PASS: "quick", VERIFIED: "checked", HEADLINE: "headline"}
TOP_WINDOW = timedelta(hours=48)  # with no lead chosen, the top-rated story of this window leads (the page always has one)
HEADLINES = 3  # the page cycles through the lead and the next top-rated stories of TOP_WINDOW (owner, 2026-11-03)


def public_story(story: dict[str, Any]) -> dict[str, Any]:
    out = {
        "id": story["id"],
        "title": story["title"],
        "summary": story["summary"] if story["status"] in (FIRST_PASS, VERIFIED) else "",
        "url": story["url"],
        "source": story["source"],
        "desk": story["desk"],
        "section": story["section"],
        "published": story["published"],
        "found": story["found_at"],
        "time_known": not story["record"].get("_date_only"),
        "shown": story["shown_at"],
        "check": BADGES.get(story["status"], ""),
        "official": bool(story["official"]),
    }
    tier = live_lead.impact_tier(story["score"]) if story["status"] in (VERIFIED, HEADLINE) else ""
    if tier:
        out["impact"] = tier
    if story["also"]:
        out["also"] = [{"source": a.get("source", ""), "url": a.get("url", "")} for a in story["also"] if a.get("url")]
    if story["record"].get("_scope") == "world":
        out["scope"] = "world"  # the page shows these only when the reader asks for everything
    if live_lead.is_war(story):
        out["war"] = True  # /bubbles/ never draws one at the largest size (owner, 2026-11-05)
    corrections = [n["note"] for n in story["notes"] if str(n.get("note", "")).startswith("Correction")]
    if corrections:
        out["correction"] = corrections[-1]
    return out


def group_stories(stories: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Every event's reports (by `group_id`, or the story's own id), and one entry per event, in the given order."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for s in stories:
        groups.setdefault(s["group_id"] or s["id"], []).append(s)
    primary = {}
    for members in groups.values():
        best = max(members, key=live_sort.primary_rank) if len(members) > 1 else members[0]
        primary.update((m["id"], best) for m in members)
    return groups, [s for s in stories if primary[s["id"]] is s]


def grouped_story(story: dict[str, Any], groups: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """The story with the group's other reports listed under it (one link per outlet)."""
    out = public_story(story)
    others = [m for m in groups.get(story["group_id"] or story["id"], []) if m["id"] != story["id"]]
    if not others:
        return out
    also, outlets = list(out.get("also", [])), {story["source"].casefold()}
    outlets |= {a["source"].casefold() for a in also}
    for m in sorted(others, key=lambda m: m["shown_at"]):
        if m["source"].casefold() not in outlets:
            outlets.add(m["source"].casefold())
            also.append({"source": m["source"], "url": m["url"], "title": m["title"]})
    out["also"] = also
    out["members"] = [m["id"] for m in others]  # a link to any report of the event finds this entry
    return out


def same_event(first: str, second: str) -> bool:
    """Two headlines that share at least three words and half the shorter one's: e.g. "Spain rail crash: How did its
    high-speed rail network reach this point?" and "Two Australians killed in Spain's second high-speed rail crash"."""
    shared = set(title_key(first).split()) & set(title_key(second).split())
    return len(shared) >= 3 and title_overlap(first, second) >= 0.5


def top_stories(
    stories: list[dict[str, Any]], now: datetime, count: int, skip: set[str] = frozenset(), after: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """The top `count` stories of the last 48 hours, one per event: double-checked stories by score, then summarized
    ones, then any, newest first within each. Groups in `skip` are left out, and so is a story whose headline shares
    half its words with one already picked or in `after` (the sorter does not always group two takes on one event, and
    the cycling headlines should be three different stories). Never a war story (live_lead.is_war)."""
    recent = [s for s in stories if (parse_iso(s["published"]) or now) >= now - TOP_WINDOW and not live_lead.is_war(s)]
    rank = lambda s: ((2, s["score"]) if s["status"] == VERIFIED and s["score"] is not None
                      else (1, 0) if s["status"] == FIRST_PASS else (0, 0), s["published"])
    picked, seen, titles = [], set(skip), list(after)
    for story in sorted(recent, key=rank, reverse=True):
        if len(picked) == count:
            break
        if (story["group_id"] or story["id"]) in seen or any(same_event(story["title"], t) for t in titles):
            continue
        seen.add(story["group_id"] or story["id"])
        titles.append(story["title"])
        picked.append(story)
    return picked


def top_story(stories: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    """The page's headline when the lead desk has none (a quiet day, or the last lead aged out)."""
    return next(iter(top_stories(stories, now, 1)), None)


def build_feed(
    store: Store,
    config: dict[str, Any],
    *,
    now: datetime,
    level: str,
    wire: list[dict[str, Any]] | None = None,
    market: dict[str, Any] | None = None,
) -> dict[str, Any]:
    site = config["site"]
    stories = store.visible(now - timedelta(hours=float(site["river_hours"])), limit=int(site.get("feed_limit", 300)))
    groups, shown = group_stories(stories)
    grouped = lambda story: grouped_story(story, groups)
    public = [grouped(s) for s in shown]
    by_id = {s["id"]: s for s in stories}

    lead_row = store.current_lead()
    lead = None
    if lead_row and lead_row["story_id"]:
        story = by_id.get(lead_row["story_id"]) or store.get(lead_row["story_id"])
        if story is not None:
            lead = {**grouped(story), "since": iso(lead_row["set_at"])}
            held = store.get_state(live_lead.OVERRIDE_KEY) or {}
            if held.get("story_id") == story["id"] and (parse_iso(str(held.get("until", ""))) or now) > now:
                lead["held_until"] = held["until"]  # the owner chose this lead; the /login page shows until when
            elif live_lead.is_war(story):
                lead = None  # chosen before war stories were kept from the lead; the owner's own pick stands
    if lead is None:
        story = top_story(stories, now)
        if story is not None:
            lead = {**grouped(story), "since": story["published"], "pick": "top"}

    day = now - timedelta(hours=24)
    lead_group = (by_id[lead["id"]]["group_id"] or lead["id"]) if lead and lead["id"] in by_id else (lead or {}).get("id")
    # The page cycles through the lead and the next top-rated stories; Developing then repeats none of them.
    others = top_stories(stories, now, HEADLINES - 1, {lead_group}, (lead["title"],)) if lead else []
    leads = ([lead] if lead else []) + [{**grouped(s), "since": s["published"], "pick": "top"} for s in others]
    headline_groups = {lead_group} | {s["group_id"] or s["id"] for s in others}
    developing = sorted(
        (s for s in shown if s["status"] == VERIFIED and (s["group_id"] or s["id"]) not in headline_groups
         and (s["score"] or 0) >= live_lead.DEVELOPING_FLOOR
         and (parse_iso(s["published"]) or now) >= now - timedelta(hours=12)),
        key=lambda s: (s["score"] or 0, s["published"]), reverse=True,
    )[:3]

    # The wire bar: the newest summarized U.S. stories, one per event (headline-only and world stories stay in the list).
    ticker = sorted((s for s in shown if s["status"] in (FIRST_PASS, VERIFIED) and s["record"].get("_scope") != "world"),
                    key=lambda s: s["shown_at"], reverse=True)[: int(site["ticker_items"])]
    desks = []
    for desk in config["desks"]:
        count = sum(1 for s in shown if s["desk"] == desk["key"] and (parse_iso(s["published"]) or now) >= day)
        desks.append({"key": desk["key"], "name": desk["name"], "today": count})

    return {
        "version": FEED_VERSION,
        "generated": iso(now),
        "level": level,
        "site": {"name": site["name"], "label": site["live_label"], "poll_seconds": int(site["poll_seconds"])},
        "overnight": config.get("push", {}).get("overnight"),  # Eastern hours when the public copy updates hourly
        "lead": lead,
        "leads": leads,
        "developing": [public_story(s)["id"] for s in developing],
        "ticker": [{"id": s["id"], "source": s["source"], "title": s["title"], "url": s["url"]} for s in ticker],
        "desks": desks,
        "stories": public,
        "market": market,
        "posts": live_posts.public_posts(store, now),
        "wire": (wire or [])[:40],
    }


def content_hash(feed: dict[str, Any]) -> str:
    """Hash of everything except the clock and the sweep line, so publishing only happens on real changes."""
    stable = {k: v for k, v in feed.items() if k not in ("generated", "wire")}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def write_site(out_dir: Path, feed: dict[str, Any]) -> Path:
    """Write feed.json atomically and refresh the page's static files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for folder, target_dir in ((ASSETS, out_dir), (LOGIN, out_dir / "login"), (DAYS, out_dir / "days"), (BUBBLES, out_dir / "bubbles")):
        target_dir.mkdir(parents=True, exist_ok=True)
        for asset in folder.iterdir():
            if asset.is_file():
                target = target_dir / asset.name
                if not target.exists() or target.read_bytes() != asset.read_bytes():
                    shutil.copyfile(asset, target)
    path = out_dir / "feed.json"
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(feed, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temp, path)
    return path
