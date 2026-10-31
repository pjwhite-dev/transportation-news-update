"""The live page's daily archive: one file per finished Eastern day, read by advancedtransportation.live/days/.

The live page holds three days. Every story that was on it stays in the store, so each finished day is written
out as days/<YYYY-MM-DD>.json (the same public fields and one-entry-per-event grouping as feed.json) with
days/index.json listing the days. The push sends them to the live-feed branch beside feed.json. A day is
rewritten for RECENT days after it ends, as late checks and grouping catch up; after that it is
written once if missing and then left alone. Today is the live page's, not the archive's.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import live_feed
from live_store import Store, iso, parse_iso

EASTERN = ZoneInfo("America/New_York")
RECENT = 3
VERSION = 1


def bounds(day: date) -> tuple[datetime, datetime]:
    """Eastern midnight to midnight (23 or 25 hours on the clock-change days)."""
    return datetime.combine(day, time(), EASTERN), datetime.combine(day + timedelta(days=1), time(), EASTERN)


def finished_days(store: Store, now: datetime) -> list[date]:
    """Every Eastern day before today that has a story, oldest first."""
    today = now.astimezone(EASTERN).date()
    days = {stamp.astimezone(EASTERN).date() for stamp in map(parse_iso, store.visible_published()) if stamp}
    return sorted(d for d in days if d < today)


def build_day(store: Store, config: dict[str, Any], day: date, now: datetime) -> dict[str, Any]:
    start, end = bounds(day)
    groups, shown = live_feed.group_stories(store.visible_between(start, end))
    counts = {d["key"]: sum(1 for s in shown if s["desk"] == d["key"]) for d in config["desks"]}
    return {
        "version": VERSION,
        "day": day.isoformat(),
        "generated": iso(now),
        "desks": [{"key": d["key"], "name": d["name"], "count": counts[d["key"]]} for d in config["desks"]],
        "stories": [live_feed.grouped_story(s, groups) for s in shown],
    }


def _write(path: Path, payload: dict[str, Any]) -> bool:
    """Write the file if its stories changed (the clock alone is not a change); True when it was written."""
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        if {**old, "generated": ""} == {**payload, "generated": ""}:
            return False
    except (OSError, ValueError):
        pass
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temp, path)
    return True


def write_days(store: Store, config: dict[str, Any], out_dir: Path, now: datetime) -> list[str]:
    """Write missing and recent days and the index; the names of the files written."""
    folder = out_dir / "days"
    folder.mkdir(parents=True, exist_ok=True)
    recent = now.astimezone(EASTERN).date() - timedelta(days=RECENT)
    written, listing = [], []
    for day in finished_days(store, now):
        path = folder / f"{day.isoformat()}.json"
        if day >= recent or not path.exists():
            payload = build_day(store, config, day, now)
            if _write(path, payload):
                written.append(path.name)
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
        listing.append({"day": day.isoformat(), "stories": len(payload["stories"])})
    if _write(folder / "index.json", {"version": VERSION, "generated": iso(now), "days": listing[::-1]}):
        written.append("index.json")
    return written
