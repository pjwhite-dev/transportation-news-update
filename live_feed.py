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
from live_store import FIRST_PASS, HEADLINE, VERIFIED, Store, iso, parse_iso

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "site_assets" / "live"
LOGIN = ROOT / "site_assets" / "login"  # the owner's override page (advancedtransportation.live/login/)
FEED_VERSION = 1
BADGES = {FIRST_PASS: "quick", VERIFIED: "checked", HEADLINE: "headline"}


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
    corrections = [n["note"] for n in story["notes"] if str(n.get("note", "")).startswith("Correction")]
    if corrections:
        out["correction"] = corrections[-1]
    return out


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
    groups: dict[str, list[dict[str, Any]]] = {}
    for s in stories:
        groups.setdefault(s["group_id"] or s["id"], []).append(s)
    primary = {}
    for members in groups.values():
        best = max(members, key=live_sort.primary_rank) if len(members) > 1 else members[0]
        primary.update((m["id"], best) for m in members)
    shown = [s for s in stories if primary[s["id"]] is s]  # one entry per event, in publication order

    def grouped(story: dict[str, Any]) -> dict[str, Any]:
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

    day = now - timedelta(hours=24)
    lead_group = (by_id[lead["id"]]["group_id"] or lead["id"]) if lead and lead["id"] in by_id else (lead or {}).get("id")
    developing = sorted(
        (s for s in shown if s["status"] == VERIFIED and (s["group_id"] or s["id"]) != lead_group
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
    for folder, target_dir in ((ASSETS, out_dir), (LOGIN, out_dir / "login")):
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
