"""Before/after comparison of a dry-run replay against the edition that was actually published.

    python scripts/replay_diff.py 2026-10-25 2026-10-26 2026-10-27 > docs/replay-diff.md

Reads data/archive/<day>.json (published) and .runtime/dryrun-<day>.json (replay); the QA report from
.runtime/dryrun-qa/qa/<day>.json (or .runtime/failed-qa/qa/) explains each removal.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def stories(edition: dict) -> dict[str, dict]:
    found = {}
    for section, items in (edition.get("sections") or {}).items():
        for item in items:
            found[str(item.get("url", ""))] = {**item, "section": section}
    return found


def qa_events(day: str) -> list[dict]:
    for base in (ROOT / ".runtime" / "dryrun-qa" / "qa", ROOT / ".runtime" / "failed-qa" / "qa"):
        data = load(base / f"{day}.json")
        if data:
            return data.get("events", [])
    return []


def first_sentences(text: str, count: int = 2) -> str:
    parts = [p.strip() for p in str(text).replace("\n", " ").split(". ") if p.strip()]
    return ". ".join(parts[:count]) + ("." if parts else "")


def report(day: str) -> str:
    published = load(ROOT / "data" / "archive" / f"{day}.json") or {}
    replay = load(ROOT / ".runtime" / f"dryrun-{day}.json")
    old = stories(published)
    lines = [f"## {day}", ""]
    events = qa_events(day)
    if replay is None:
        lines += [f"The replay was **refused** by the thin-edition gate. Published edition had {len(old)} stories.", ""]
        replay_stories: dict[str, dict] = {}
    else:
        replay_stories = stories(replay)
    lines += [f"Published: **{len(old)}** stories. Replay: **{len(replay_stories)}** stories.", ""]
    by_url = {e.get("url"): e for e in events if e.get("url")}
    removed = [s for u, s in old.items() if u not in replay_stories]
    added = [s for u, s in replay_stories.items() if u not in old]
    if removed:
        lines += ["### In the published edition, not in the replay", ""]
        for story in removed:
            event = by_url.get(story["url"])
            reason = f"{event['stage']}: {event['reason']}" if event else "not selected after the stricter candidate checks"
            lines.append(f"- {story['title'][:110]} ({story.get('source', '')}) — {reason[:200]}")
        lines.append("")
    if added:
        lines += ["### New in the replay", ""]
        for story in added:
            lines.append(f"- {story['title'][:110]} ({story.get('source', '')}, {str(story.get('published', ''))[:10]})")
        lines.append("")
    if replay:
        lines += ["### Executive Summary, before and after", "",
                  "**Published:** " + first_sentences(published.get("executive_summary", ""), 2), "",
                  "**Replay:** " + first_sentences(replay.get("executive_summary", ""), 2), ""]
        methods: dict[str, int] = {}
        for story in replay_stories.values():
            methods[story.get("summary_method", "feed-text")] = methods.get(story.get("summary_method", "feed-text"), 0) + 1
        lines += [f"Summary methods in the replay: {methods}", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    print("# Replay of the 2026-10-25 to 2026-10-27 editions: before and after\n")
    for day in sys.argv[1:]:
        print(report(day))
