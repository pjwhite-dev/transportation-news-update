from __future__ import annotations

import re
from difflib import SequenceMatcher
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


HISTORY_LOOKBACK_DAYS = 45

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_NUMBER_PATTERN = re.compile(
    r"(?:\$\s*)?\b\d+(?:\.\d+)?(?:\s*(?:million|billion|percent|%|miles?|"
    r"vehicles?|aircraft|systems?|sites?|states?|cities?|flights?))?\b",
    re.IGNORECASE,
)
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by",
    "for", "from", "has", "have", "in", "into", "is", "it", "its", "new",
    "of", "on", "or", "says", "said", "that", "the", "their", "this", "to",
    "under", "up", "with", "will", "after", "about", "amid", "could", "may",
    "news", "report", "reports", "update", "updates", "latest", "today",
}

_MILESTONE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("final rule", re.compile(r"\b(?:final rule|finalizes?|finalized)\b", re.I)),
    ("proposed rule", re.compile(r"\b(?:proposed rule|nprm|proposes?|proposed)\b", re.I)),
    ("approval", re.compile(r"\b(?:approv(?:al|es?|ed)|authoriz(?:es?|ed|ation)|certif(?:ies|ied|ication))\b", re.I)),
    ("contract award", re.compile(r"\b(?:contract (?:award|awarded)|awards? .* contract|selected for .* contract)\b", re.I)),
    ("operational launch", re.compile(r"\b(?:launch(?:es|ed)?|begins? (?:service|operations)|starts? (?:service|operations)|enters? service|deploys?|deployed)\b", re.I)),
    ("first operation", re.compile(r"\b(?:first|inaugural) (?:flight|delivery|operation|deployment|test|service)\b", re.I)),
    ("test result", re.compile(r"\b(?:completes?|completed|successful(?:ly)?) (?:a |an )?(?:flight|test|trial|demonstration)\b", re.I)),
    ("deadline change", re.compile(r"\b(?:deadline|comment period) (?:extended|closes?|closed|reopened)\b", re.I)),
    ("permit", re.compile(r"\b(?:permit|waiver|exemption) (?:granted|approved|issued|denied)\b", re.I)),
    ("safety action", re.compile(r"\b(?:recall|investigation|enforcement action|safety order|emergency order)\b", re.I)),
    ("legislative action", re.compile(r"\b(?:signed into law|passes? (?:the )?(?:house|senate)|enacted|vetoed)\b", re.I)),
)

_TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source", "utm_campaign",
    "utm_content", "utm_medium", "utm_source", "utm_term",
}


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


@lru_cache(maxsize=32768)
def canonical_url(value: str) -> str:
    """Normalize ordinary article URLs without trying to unwrap publisher redirects."""
    value = _clean_text(value)
    if not value:
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in _TRACKING_QUERY_KEYS
        and not key.casefold().startswith("utm_")
    ]
    return urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            parts.path.rstrip("/"),
            urlencode(query),
            "",
        )
    )


def story_text(record: dict[str, Any]) -> str:
    return _clean_text(
        " ".join(
            str(record.get(key, ""))
            for key in ("title", "summary", "description", "pasted_context")
        )
    )


def title_tokens(record: dict[str, Any]) -> frozenset[str]:
    return _title_tokens(_clean_text(record.get("title", "")).casefold())


def title_entity_tokens(record: dict[str, Any]) -> frozenset[str]:
    """Return capitalized title tokens useful for same-day wire-story matching."""
    title = _clean_text(record.get("title", ""))
    return frozenset(
        token.casefold()
        for token in re.findall(r"\b[A-Z][A-Za-z0-9]*(?:\.[A-Za-z0-9]+)*\b", title)
        if len(token) > 2 and token.casefold() not in _STOP_WORDS
    )


@lru_cache(maxsize=16384)
def _title_tokens(title: str) -> frozenset[str]:
    return frozenset(
        {
            token
            for token in _TOKEN_PATTERN.findall(title)
            if len(token) > 2 and token not in _STOP_WORDS
        }
    )


def normalized_title(record: dict[str, Any]) -> str:
    return " ".join(sorted(title_tokens(record)))


def noteworthy_signals(record: dict[str, Any]) -> set[str]:
    text = story_text(record)
    return {
        label for label, pattern in _MILESTONE_PATTERNS if pattern.search(text)
    }


def numeric_facts(record: dict[str, Any]) -> set[str]:
    return {
        re.sub(r"\s+", " ", match.group(0).casefold()).strip()
        for match in _NUMBER_PATTERN.finditer(story_text(record))
        if len(match.group(0).strip()) > 1
    }


def story_similarity(current: dict[str, Any], previous: dict[str, Any]) -> float:
    current_url = canonical_url(current.get("url", ""))
    previous_url = canonical_url(previous.get("url", ""))
    if current_url and current_url == previous_url:
        return 1.0

    current_tokens = title_tokens(current)
    previous_tokens = title_tokens(previous)
    if not current_tokens or not previous_tokens:
        return 0.0

    intersection = current_tokens & previous_tokens
    if len(intersection) < 3:
        return 0.0

    union = current_tokens | previous_tokens
    jaccard = len(intersection) / len(union)
    containment = len(intersection) / min(len(current_tokens), len(previous_tokens))
    sequence = SequenceMatcher(
        None,
        _clean_text(current.get("title", "")).casefold(),
        _clean_text(previous.get("title", "")).casefold(),
    ).ratio()
    return max(sequence, jaccard, containment * 0.94)


def same_covered_event(
    current: dict[str, Any],
    previous: dict[str, Any],
    *,
    require_same_section: bool = True,
) -> tuple[bool, float]:
    score = story_similarity(current, previous)
    shared = title_tokens(current) & title_tokens(previous)
    exact_url = bool(
        canonical_url(current.get("url", ""))
        and canonical_url(current.get("url", ""))
        == canonical_url(previous.get("url", ""))
    )
    same_section = (
        not current.get("section")
        or not previous.get("section")
        or current.get("section") == previous.get("section")
    )
    same_day_named_event = bool(
        not require_same_section
        and len(shared) >= 3
        and len(title_entity_tokens(current) & title_entity_tokens(previous)) >= 3
        and score >= 0.45
    )
    matched = exact_url or (
        (same_section or not require_same_section)
        and len(shared) >= 4
        and (
            score >= (0.7 if not require_same_section else 0.78)
            or len(shared) >= 6
        )
    ) or same_day_named_event
    return matched, score


def noteworthy_update_reason(
    current: dict[str, Any], previous: dict[str, Any]
) -> str:
    new_signals = sorted(noteworthy_signals(current) - noteworthy_signals(previous))
    if new_signals:
        return ", ".join(new_signals)

    new_numbers = sorted(numeric_facts(current) - numeric_facts(previous))
    if new_numbers and noteworthy_signals(current):
        return "new reported result: " + ", ".join(new_numbers[:2])
    return ""


def find_previous_coverage(
    record: dict[str, Any],
    previous_records: Iterable[dict[str, Any]],
    *,
    require_same_section: bool = True,
) -> tuple[dict[str, Any] | None, float, str]:
    best: dict[str, Any] | None = None
    best_score = 0.0
    for previous in previous_records:
        if require_same_section and (
            record.get("section")
            and previous.get("section")
            and record.get("section") != previous.get("section")
        ):
            continue
        matched, score = same_covered_event(
            record,
            previous,
            require_same_section=require_same_section,
        )
        if matched and score > best_score:
            best = previous
            best_score = score
    if best is None:
        return None, 0.0, ""
    return best, best_score, noteworthy_update_reason(record, best)


def annotate_previous_coverage(
    records: Iterable[dict[str, Any]],
    previous_records: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Annotate likely repeat events while preserving every input record."""
    previous = list(previous_records)
    annotated: list[dict[str, Any]] = []
    for source_record in records:
        record = dict(source_record)
        match, score, update_reason = find_previous_coverage(record, previous)
        if match is not None:
            record["previous_coverage"] = {
                "date": match.get("coverage_date", match.get("date_label", "")),
                "title": match.get("title", ""),
                "url": match.get("url", ""),
                "similarity": round(score, 3),
            }
            record["previously_covered"] = not bool(update_reason)
            record["noteworthy_new_development"] = update_reason
        else:
            record["previously_covered"] = False
            record["noteworthy_new_development"] = ""
        annotated.append(record)
    return annotated


def filter_previously_covered(
    records: Iterable[dict[str, Any]],
    previous_records: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    included: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for record in annotate_previous_coverage(records, previous_records):
        if record.get("previously_covered") and not record.get("required_include"):
            suppressed.append(record)
        else:
            included.append(record)
    return included, suppressed


def briefing_stories(briefing: dict[str, Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    stories: list[dict[str, Any]] = []
    for section, items in briefing.get("sections", {}).items():
        for item in items:
            identity = item.get("id") or canonical_url(item.get("url", ""))
            if not identity or identity in seen:
                continue
            seen.add(identity)
            record = dict(item)
            record.setdefault("section", section)
            record.setdefault("coverage_date", _briefing_date(briefing).isoformat())
            stories.append(record)
    return stories


def _briefing_date(briefing: dict[str, Any]) -> date:
    for key in ("window_end", "generated_at"):
        try:
            return datetime.fromisoformat(str(briefing.get(key, ""))).date()
        except ValueError:
            continue
    return date.min


def load_published_history(
    archive_dir: Path,
    before: date,
    lookback_days: int = HISTORY_LOOKBACK_DAYS,
) -> list[dict[str, Any]]:
    earliest = before - timedelta(days=lookback_days)
    stories: list[dict[str, Any]] = []
    if not archive_dir.exists():
        return stories

    for path in sorted(archive_dir.glob("*.json"), reverse=True):
        try:
            edition_date = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if edition_date >= before or edition_date < earliest:
            continue
        try:
            import json

            briefing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stories.extend(briefing_stories(briefing))
    return stories
