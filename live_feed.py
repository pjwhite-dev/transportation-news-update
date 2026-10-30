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
    public = [public_story(s) for s in stories]
    by_id = {s["id"]: s for s in stories}

    lead_row = store.current_lead()
    lead = None
    if lead_row and lead_row["story_id"]:
        story = by_id.get(lead_row["story_id"]) or store.get(lead_row["story_id"])
        if story is not None:
            lead = {**public_story(story), "since": iso(lead_row["set_at"])}
            held = store.get_state(live_lead.OVERRIDE_KEY) or {}
            if held.get("story_id") == story["id"] and (parse_iso(str(held.get("until", ""))) or now) > now:
                lead["held_until"] = held["until"]  # the owner chose this lead; the /login page shows until when

    day = now - timedelta(hours=24)
    developing = sorted(
        (s for s in stories if s["status"] == VERIFIED and (not lead or s["id"] != lead["id"])
         and (s["score"] or 0) >= live_lead.DEVELOPING_FLOOR
         and (parse_iso(s["published"]) or now) >= now - timedelta(hours=12)),
        key=lambda s: (s["score"] or 0, s["published"]), reverse=True,
    )[:3]

    ticker = sorted(stories, key=lambda s: s["shown_at"], reverse=True)[: int(site["ticker_items"])]
    desks = []
    for desk in config["desks"]:
        count = sum(1 for s in stories if s["desk"] == desk["key"] and (parse_iso(s["published"]) or now) >= day)
        desks.append({"key": desk["key"], "name": desk["name"], "today": count})

    return {
        "version": FEED_VERSION,
        "generated": iso(now),
        "level": level,
        "site": {"name": site["name"], "label": site["live_label"], "tagline": site.get("tagline", ""), "poll_seconds": int(site["poll_seconds"])},
        "lead": lead,
        "developing": [public_story(s)["id"] for s in developing],
        "ticker": [{"id": s["id"], "source": s["source"], "title": s["title"], "url": s["url"]} for s in ticker],
        "desks": desks,
        "stories": public,
        "market": market,
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
