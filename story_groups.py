"""Same-event grouping inside an edition and against the last seven days of editions.

Identifiers (Federal Register document number, RIN, docket, House report, URL) decide first; headline
similarity decides only when no identifier is shared. Inside one edition, two stories in the same section
that name the same place, company or figure but are worded differently ("Spain mourns after latest train
tragedy kills 68" / "Dozens dead in Spain after high-speed train slams into another") are put to the local
verifier as a yes/no question, a bounded number of times. The "embedding" kept in the store is a lexical
signature (title tokens and numbers), because no embedding model is installed.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from coverage_history import (
    canonical_url,
    noteworthy_signals,
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


_UNIT_NUMBER = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:million|billion))?"
    r"|\d[\d,]*(?:\.\d+)?\s?(?:%|percent|million|billion|vehicles?|aircraft|drones?|flights?|miles?|jobs|states)\b",
    re.I,
)


_DESIGNATOR = re.compile(r"\b(?:part|parts|no\.?|nos\.?|section|sec\.?|fmvss|docket|title|rept\.?|h\.r\.?)\s*\d[\d.-]*", re.I)


def unit_numbers(story: dict[str, Any]) -> set[str]:
    """Figures with a unit or currency (rule numbers like 'No. 108' are identifiers, not new facts)."""
    text = f"{story.get('title', '')} {story.get('summary', '')}"
    text = _DESIGNATOR.sub(" ", text)
    return {re.sub(r"\s+", " ", m.group(0).casefold()).replace(",", "") for m in _UNIT_NUMBER.finditer(text)}


def _rulemaking_terms() -> list[tuple[str, list[str]]]:
    try:
        import yaml

        entries = yaml.safe_load(_RULE_TERMS_PATH.read_text(encoding="utf-8"))["rulemakings"]
    except (OSError, ValueError, KeyError):
        return []
    return [(e.get("rin") or e["id"], [t.casefold() for t in e.get("match_terms", [])]) for e in entries if e.get("match_terms")]


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


MAX_JUDGED_PAIRS = 40
_NAME = re.compile(r"\b[A-Z][A-Za-z-]{3,}\b")
_NOT_NAMES = {
    "the", "this", "that", "these", "with", "from", "after", "over", "into", "about", "what", "when", "where",
    "here", "today", "news", "first", "second", "third", "new", "latest", "update", "more", "than", "says",
    "said", "will", "could", "would", "their", "they", "report", "reports", "week", "year", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february", "march", "april", "june",
    "july", "august", "september", "october", "november", "december", "drone", "drones", "robot", "robots",
    "robotics", "rail", "train", "trains", "federal", "government", "company", "military", "army", "president",
}
SAME_EVENT_SYSTEM = """You decide whether two news reports describe the SAME concrete event: the same crash,
attack, announcement, rule, contract, launch, deployment, vote or decision. Different events that involve the
same company, country or technology are NOT the same event (two different Waymo city launches, two different
drone strikes on different days, a crash and a separate analysis of rail safety). A follow-up report on the
same event (a rising death toll, reactions, mourning) IS the same event. Respond with JSON only."""
SAME_EVENT_SCHEMA = {
    "type": "object",
    "properties": {"same_event": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["same_event", "reason"],
    "additionalProperties": False,
}


def key_terms(story: dict[str, Any]) -> set[str]:
    """Names (places, companies, agencies) and figures in a story's headline and the start of its summary."""
    text = f"{story.get('title', '')}. {str(story.get('summary', ''))[:300]}"
    names = {m.group(0).casefold() for m in _NAME.finditer(text)} - _NOT_NAMES
    return names | unit_numbers(story)


def worth_judging(first: dict[str, Any], second: dict[str, Any]) -> int:
    """How many names or figures two stories in the same section share (0: not worth asking)."""
    if first.get("section") != second.get("section"):
        return 0
    return len(key_terms(first) & key_terms(second))


def make_same_event_check(verifier: Any) -> Callable[[dict[str, Any], dict[str, Any]], tuple[bool, str]]:
    """Wrap a local provider as same_event(first, second) -> (same, reason)."""

    def describe(story: dict[str, Any]) -> str:
        return f"Headline: {story.get('title', '')}\nSource: {story.get('source', '')}\nSummary: {str(story.get('summary', ''))[:600]}"

    def judge(first: dict[str, Any], second: dict[str, Any]) -> tuple[bool, str]:
        result = verifier.generate_structured(
            [{"role": "system", "content": SAME_EVENT_SYSTEM},
             {"role": "user", "content": f"REPORT A\n{describe(first)}\n\nREPORT B\n{describe(second)}"}],
            schema_name="same_event", schema=SAME_EVENT_SCHEMA, max_output_tokens=800,
        )
        return bool(result.value.get("same_event")), str(result.value.get("reason", ""))[:120]

    return judge


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


MAX_ALSO_COVERED = 6


def _also(story: dict[str, Any]) -> list[dict[str, str]]:
    return [dict(e) if isinstance(e, dict) else {"source": "Related coverage", "url": str(e)} for e in story.get("also_covered") or []]


def merge_group(members: list[dict[str, Any]]) -> dict[str, Any]:
    """One item for a group: the best-tier, most important member leads; the others become additional coverage."""
    ordered = sorted(members, key=lambda s: (source_tier(s), -int(s.get("importance", 0) or 0)))
    lead = dict(ordered[0])
    seen = {canonical_url(str(lead.get("url", "")))}
    outlets = {str(lead.get("source", "")).casefold()}
    extras: list[dict[str, str]] = []
    for member in ordered:
        candidates = _also(member) if member is not ordered[0] else _also(lead)
        if member is not ordered[0]:
            candidates = [{"source": str(member.get("source", "Related coverage")), "url": str(member.get("url", ""))}, *candidates]
        for extra in candidates:
            key = canonical_url(str(extra.get("url", "")))
            outlet = str(extra.get("source", "")).casefold()
            if key and key not in seen and (outlet == "related coverage" or outlet not in outlets):
                seen.add(key)
                outlets.add(outlet)
                extras.append(extra)
    # One link per outlet, at most MAX_ALSO_COVERED (the Spain crash collected 14, one outlet twice); official first.
    lead["also_covered"] = sorted(extras, key=lambda e: not e.get("official"))[:MAX_ALSO_COVERED]
    lead["importance"] = max(int(m.get("importance", 0) or 0) for m in members)
    lead["group_size"] = len(members)
    return lead


def group_within_edition(
    stories: list[dict[str, Any]],
    judge: Callable[[dict[str, Any], dict[str, Any]], tuple[bool, str]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Union stories that report the same event; returns (one item per group, merge log).

    Identifiers and near-identical headlines decide first. Then, with a `judge`, same-section pairs that share
    a name or figure are asked about, most shared terms first, at most MAX_JUDGED_PAIRS times.
    """
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
    if judge is not None:
        pairs = sorted(
            ((worth_judging(stories[i], stories[j]), i, j) for i in range(len(stories)) for j in range(i + 1, len(stories))),
            key=lambda p: (-p[0], p[1], p[2]),
        )
        asked = 0
        for shared, i, j in pairs:
            if not shared or asked >= MAX_JUDGED_PAIRS:
                break
            if find(i) == find(j):
                continue
            asked += 1
            try:
                same, why = judge(stories[i], stories[j])
            except Exception:  # a verifier hiccup leaves the stories apart, as before
                continue
            if same:
                parent[find(j)] = find(i)
                reasons[j] = f"verifier: {why}"
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
        "numbers": sorted(unit_numbers(story)),
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
    """Canonical URLs of the story that led an edition's Executive Summary.

    Editions with numbered citation links use the links in the first sentence. Older editions have none,
    so the story whose headline words mostly appear in the first sentence is taken as the lead.
    """
    text = str(briefing.get("executive_summary", "")).strip()
    first = re.split(r"(?<=[.!?])(?:\[\d+\]\([^)]*\))*\s+(?=[A-Z])", text, maxsplit=1)[0] if text else ""
    linked = {canonical_url(u) for u in re.findall(r"\]\((https?://[^)\s]+)\)", first)}
    if linked or not first:
        return linked
    words = set(re.findall(r"[a-z0-9]{4,}", first.casefold()))
    leads: set[str] = set()
    for items in (briefing.get("sections") or {}).values():
        for item in items:
            tokens = set(re.findall(r"[a-z0-9]{4,}", str(item.get("title", "")).casefold()))
            if len(tokens) >= 3 and len(tokens & words) / len(tokens) >= 0.6:
                leads.add(canonical_url(str(item.get("url", ""))))
    return leads


def write_store(root: Path, store: list[dict[str, Any]], today: date) -> Path:
    path = root / "data" / "published_groups.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"as_of": today.isoformat(), "window_days": STORE_DAYS, "groups": store}, indent=1), encoding="utf-8")
    return path


def previous_match(story: dict[str, Any], store: list[dict[str, Any]]) -> dict[str, Any] | None:
    ids = identifiers(story)
    strong = _strong(ids)
    urls = {i[4:] for i in ids if i.startswith("url:")}
    # Dropping a story is stricter than hinting the model about one: sections are ignored (a story can be
    # re-sectioned) but the headline thresholds are the strict ones, so two Archer stories about different
    # events are not treated as the same event.
    candidate = {"title": story.get("title", ""), "url": story.get("url", ""), "section": ""}
    for entry in store:
        if (strong & set(entry.get("identifiers", []))) or (urls & set(entry.get("urls", []))):
            return entry
        previous = {"title": entry["title"], "url": entry["url"], "section": ""}
        matched, _ = same_covered_event(candidate, previous, require_same_section=True)
        if matched:
            return entry
    return None


# "68 dead", "killing at least 30", "kills 68": a casualty count. 2026-10-29: the Spain crash rose from 30 to 68
# dead overnight; the merged report linked yesterday's El País story and was dropped as a repeat.
_TOLL = re.compile(
    r"\b(\d[\d,]*)\s+(?:people\s+|passengers\s+)?(?:dead|killed|deaths|injured|wounded)\b"
    r"|\b(?:kill(?:s|ed|ing)|death toll (?:of|rises to|reaches))\s+(?:at least\s+)?(\d[\d,]*)\b", re.I)


def casualty_counts(story: dict[str, Any]) -> set[str]:
    text = f"{story.get('title', '')} {story.get('summary', '')}"
    return {(m.group(1) or m.group(2)).replace(",", "") for m in _TOLL.finditer(text)}


def new_facts_since(story: dict[str, Any], entry: dict[str, Any]) -> str:
    """A reason the story is a genuine update of an earlier group, or '' when it only repeats it."""
    before = casualty_counts({"title": entry["title"], "summary": entry.get("summary", "")})
    toll = sorted(casualty_counts(story) - before)
    if before and toll:
        return "new casualty count: " + ", ".join(toll[:2])
    old = set(entry.get("numbers", []))
    now = unit_numbers(story)
    fresh = sorted(now - old)
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
