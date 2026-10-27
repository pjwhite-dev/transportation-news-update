"""Same-event grouping inside an edition and against the last seven days of editions.

Identifiers (Federal Register document number, RIN, docket, House report, URL) decide first; headline
similarity decides only when no identifier is shared. Nothing here calls a model. The "embedding" kept in
the store is a lexical signature (title tokens and numbers), because no embedding model is installed.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from coverage_history import (
    canonical_url,
    noteworthy_signals,
    numeric_facts,
    same_covered_event,
    title_tokens,
)
from editorial_config import load_config

STORE_DAYS = 7
_FR_DOC = re.compile(r"\b(20\d\d-\d{5})\b")
_RIN = re.compile(r"\b(\d{4}-A[A-Z]\d{2})\b")
_DOCKET = re.compile(r"\b([A-Z]{2,6}-\d{4}-\d{3,5})\b")
_FCC_DOCKET = re.compile(r"\b(?:SB|GN|WC|ET|MB|CG|PS|WT|IB) Docket No\.? ?(\d{2}-\d{1,4})\b", re.I)
_HOUSE_REPORT = re.compile(r"\bH(?:ouse)?\.? ?Rept?\.? ?(\d{3}-\d{1,4})\b|\bhouse-report/(\d+)\b", re.I)
_RULE_TERMS_PATH = Path(__file__).resolve().parent / "tracker" / "rulemakings.yaml"


def _rulemaking_terms() -> list[tuple[str, list[str]]]:
    try:
        import yaml

        entries = yaml.safe_load(_RULE_TERMS_PATH.read_text(encoding="utf-8"))["rulemakings"]
    except (OSError, ValueError, KeyError):
        return []
    return [(e["rin"], [t.casefold() for t in e.get("match_terms", [])]) for e in entries if e.get("match_terms")]


def identifiers(story: dict[str, Any]) -> set[str]:
    """Strong same-event identifiers from the headline, summary, links and the lead of the article."""
    urls = [str(story.get("url", ""))] + [
        str(extra.get("url", "") if isinstance(extra, dict) else extra) for extra in story.get("also_covered") or []
    ]
    text = " ".join([str(story.get("title", "")), str(story.get("summary", "")), " ".join(urls),
                     str(story.get("page_text", ""))[:1500]])
    found: set[str] = set()
    found |= {f"fr:{m}" for m in _FR_DOC.findall(text)}
    found |= {f"rin:{m}" for m in _RIN.findall(text)}
    found |= {f"docket:{m}" for m in _DOCKET.findall(text)}
    found |= {f"fcc:{m}" for m in _FCC_DOCKET.findall(text)}
    for match in _HOUSE_REPORT.finditer(text):
        found.add(f"hrept:{match.group(1) or match.group(2)}")
    headline = f"{story.get('title', '')} {story.get('summary', '')}".casefold()
    for rin, terms in _rulemaking_terms():
        if any(term in headline for term in terms):
            found.add(f"rin:{rin}")
    found |= {f"url:{canonical_url(u)}" for u in urls if u.startswith(("http://", "https://"))}
    return found


def _strong(ids: set[str]) -> set[str]:
    return {i for i in ids if not i.startswith("url:")}


def same_event(first: dict[str, Any], second: dict[str, Any]) -> tuple[bool, str]:
    a, b = identifiers(first), identifiers(second)
    shared = a & b
    if shared:
        return True, "shared " + sorted(shared)[0]
    matched, score = same_covered_event(first, second, require_same_section=False)
    return (True, f"similar headline ({score:.2f})") if matched else (False, "")


def source_tier(story: dict[str, Any]) -> int:
    from urllib.parse import urlparse

    tiers = load_config()["source_tiers"]
    host = urlparse(str(story.get("url", ""))).netloc.casefold().removeprefix("www.")
    if host in tiers["tier1_hosts"] or any(host.endswith(s) for s in tiers["tier1_suffixes"]):
        return 1
    if any(host == h or host.endswith("." + h) for h in tiers["tier2_hosts"]):
        return 2
    return 3


def _also(story: dict[str, Any]) -> list[dict[str, str]]:
    return [dict(e) if isinstance(e, dict) else {"source": "Related coverage", "url": str(e)} for e in story.get("also_covered") or []]


def merge_group(members: list[dict[str, Any]]) -> dict[str, Any]:
    """One item for a group: the best-tier, most important member leads; the others become additional coverage."""
    ordered = sorted(members, key=lambda s: (source_tier(s), -int(s.get("importance", 0) or 0)))
    lead = dict(ordered[0])
    seen = {canonical_url(str(lead.get("url", "")))}
    extras: list[dict[str, str]] = []
    for member in ordered:
        candidates = _also(member) if member is not ordered[0] else _also(lead)
        if member is not ordered[0]:
            candidates = [{"source": str(member.get("source", "Related coverage")), "url": str(member.get("url", ""))}, *candidates]
        for extra in candidates:
            key = canonical_url(str(extra.get("url", "")))
            if key and key not in seen:
                seen.add(key)
                extras.append(extra)
    lead["also_covered"] = extras
    lead["importance"] = max(int(m.get("importance", 0) or 0) for m in members)
    lead["group_size"] = len(members)
    return lead


def group_within_edition(stories: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Union stories that report the same event; returns (one item per group, merge log)."""
    parent = list(range(len(stories)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    reasons: dict[int, str] = {}
    for i in range(len(stories)):
        for j in range(i + 1, len(stories)):
            same, why = same_event(stories[i], stories[j])
            if same and find(i) != find(j):
                parent[find(j)] = find(i)
                reasons[j] = why
    buckets: dict[int, list[int]] = {}
    for index in range(len(stories)):
        buckets.setdefault(find(index), []).append(index)
    merged: list[tuple[int, dict[str, Any]]] = []
    log: list[dict[str, str]] = []
    for members in buckets.values():
        group = [stories[i] for i in members]
        item = merge_group(group) if len(group) > 1 else group[0]
        merged.append((min(members), item))
        for i in members[1:]:
            log.append({"stage": "group", "title": str(stories[i].get("title", ""))[:140], "url": str(stories[i].get("url", "")),
                        "reason": f"same event as '{str(stories[members[0]].get('title', ''))[:70]}' ({reasons.get(i, '')}); folded into Additional coverage"})
    return [item for _, item in sorted(merged, key=lambda pair: pair[0])], log


def store_entry(story: dict[str, Any], edition: str) -> dict[str, Any]:
    return {
        "edition": edition,
        "title": str(story.get("title", "")),
        "section": str(story.get("section", "")),
        "url": str(story.get("url", "")),
        "urls": sorted({canonical_url(str(story.get("url", "")))} | {
            canonical_url(str(e.get("url", "") if isinstance(e, dict) else e)) for e in story.get("also_covered") or []}),
        "identifiers": sorted(_strong(identifiers(story))),
        "tokens": sorted(title_tokens(story)),
        "numbers": sorted(numeric_facts(story)),
        "summary": str(story.get("summary", "")),
        "led": bool(story.get("led_summary", False)),
    }


def build_store(archive_dir: Path, today: date, days: int = STORE_DAYS) -> list[dict[str, Any]]:
    """Groups published in the editions of the last ``days`` days before ``today`` (derived from the archive)."""
    entries: list[dict[str, Any]] = []
    if not archive_dir.exists():
        return entries
    for path in sorted(archive_dir.glob("*.json")):
        try:
            edition = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if not (today - timedelta(days=days) <= edition < today):
            continue
        try:
            briefing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        leads = lead_urls(briefing)
        for section, items in (briefing.get("sections") or {}).items():
            for item in items:
                entry = store_entry({**item, "section": section}, path.stem)
                entry["led"] = canonical_url(str(item.get("url", ""))) in leads
                entries.append(entry)
    return entries


def lead_urls(briefing: dict[str, Any]) -> set[str]:
    """Canonical URLs cited by the first sentence of an edition's Executive Summary (the story that led it)."""
    text = str(briefing.get("executive_summary", "")).strip()
    first = re.split(r"(?<=[.!?])(?:\[\d+\]\([^)]*\))*\s+(?=[A-Z])", text, maxsplit=1)[0] if text else ""
    return {canonical_url(u) for u in re.findall(r"\]\((https?://[^)\s]+)\)", first)}


def write_store(root: Path, store: list[dict[str, Any]], today: date) -> Path:
    path = root / "data" / "published_groups.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"as_of": today.isoformat(), "window_days": STORE_DAYS, "groups": store}, indent=1), encoding="utf-8")
    return path


def previous_match(story: dict[str, Any], store: list[dict[str, Any]]) -> dict[str, Any] | None:
    ids = identifiers(story)
    strong = _strong(ids)
    urls = {i[4:] for i in ids if i.startswith("url:")}
    candidate = {"title": story.get("title", ""), "url": story.get("url", ""), "section": story.get("section", "")}
    for entry in store:
        if (strong & set(entry.get("identifiers", []))) or (urls & set(entry.get("urls", []))):
            return entry
        previous = {"title": entry["title"], "url": entry["url"], "section": entry.get("section", "")}
        matched, _ = same_covered_event(candidate, previous, require_same_section=False)
        if matched:
            return entry
    return None


def new_facts_since(story: dict[str, Any], entry: dict[str, Any]) -> str:
    """A reason the story is a genuine update of an earlier group, or '' when it only repeats it."""
    old = set(entry.get("numbers", []))
    now = numeric_facts(story)
    fresh = sorted(n for n in now - old if len(n) > 2)
    signals = noteworthy_signals(story) - noteworthy_signals({"title": entry["title"], "summary": entry.get("summary", "")})
    if signals:
        return "new development: " + ", ".join(sorted(signals))
    if fresh and noteworthy_signals(story):
        return "new figures: " + ", ".join(fresh[:2])
    return ""


def drop_repeats(
    stories: list[dict[str, Any]], store: list[dict[str, Any]], today: date
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Leave out groups already published in the last seven days unless they carry new facts."""
    kept: list[dict[str, Any]] = []
    log: list[dict[str, str]] = []
    prior = [e for e in store if e["edition"] != today.isoformat()]
    for story in stories:
        match = None if story.get("required_include") else previous_match(story, prior)
        if match is None:
            kept.append(story)
            continue
        reason = new_facts_since(story, match)
        if reason:
            kept.append({**story, "update_of": {"edition": match["edition"], "title": match["title"], "reason": reason}})
        else:
            log.append({"stage": "cross-edition", "title": str(story.get("title", ""))[:140], "url": str(story.get("url", "")),
                        "reason": f"already published {match['edition']} as '{match['title'][:70]}'; no new facts"})
    return kept, log
