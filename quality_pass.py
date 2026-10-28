"""The quality pass: runs between story assembly and the Executive Summary.

Each step is deterministic or a bounded local-model check, and each drop or rewrite is logged to the QA
report. Order matters: only stories that pass every step reach the Executive Summary.

    source labels -> section scope -> same-event groups -> repeats from earlier editions
    -> grounded summaries -> house style -> section scope again -> lead eligibility and priority
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from editorial_checks import enforce_section_scope, source_label, style_text
from grounding import GroundingRecord, ground_summary
from qa_report import QAReport
from story_groups import drop_repeats, group_within_edition, previous_match, new_facts_since

POLICY_SECTIONS = {"Federal Policy & Implementation", "Federal Register", "Surface Transportation Reauthorization"}
FOREIGN_MILITARY_SECTIONS = {"Military UAS", "International Security"}
LEAD_LOOKBACK_EDITIONS = 2
POLICY_ACTION = re.compile(
    r"\b(proposed rule|proposes?|proposal|final rule|interim final|rulemaking|notice of|executive order|advisory circular|"
    r"guidance|regulations?|legislation|bill|act|approves?|approved|approval|grants?|funding|awards?|waiver|exemption|"
    r"standards?|directive|mandate|certif\w+|authoriz\w+|order)\b", re.I)


@dataclass
class QualityContext:
    report: QAReport
    today: date
    store: list[dict[str, Any]] = field(default_factory=list)
    write: Callable[[str, str, str], str] | None = None
    entail: Callable[[str, list[str]], list[tuple[bool, str]]] | None = None
    source_texts: dict[str, str] = field(default_factory=dict)


def _flatten(sections: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    stories: list[dict[str, Any]] = []
    for section, items in sections.items():
        for item in items:
            stories.append({**item, "section": item.get("section") or section})
    return stories


def _regroup(stories: list[dict[str, Any]], template: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    sections: dict[str, list[dict[str, Any]]] = {name: [] for name in template}
    for story in sorted(stories, key=lambda s: (int(s.get("importance", 0) or 0), str(s.get("published", ""))), reverse=True):
        sections.setdefault(story["section"], []).append(story)
    return sections


def priority_tier(story: dict[str, Any]) -> int:
    """1 federal policy actions, 2 company and industry news, 3 foreign and military news."""
    from story_groups import source_tier

    text = f"{story.get('title', '')} {story.get('summary', '')}"
    if story.get("origin") == "Federal Register API" or story.get("section") in POLICY_SECTIONS:
        return 1
    # A government source alone is not a policy action: a NASA flight-test photo story is not one.
    if source_tier(story) == 1 and POLICY_ACTION.search(text):
        return 1
    if story.get("section") in FOREIGN_MILITARY_SECTIONS:
        return 3
    return 2


def quality_pass(sections: dict[str, list[dict[str, Any]]], ctx: QualityContext) -> dict[str, list[dict[str, Any]]]:
    report = ctx.report
    template = sections
    stories = _flatten(sections)
    report.note("stories_entering_quality_pass", len(stories))

    # 1. Clean publication names; syndicated copies show the original publisher.
    for story in stories:
        story["source"] = source_label(str(story.get("source", "")), str(story.get("url", "")),
                                       str(story.get("page_original_publisher", "")))
        for extra in story.get("also_covered") or []:
            if isinstance(extra, dict):
                extra["source"] = source_label(str(extra.get("source", "")), str(extra.get("url", "")))

    # 2. Section scope (cheap, so run before spending model time).
    scoped, log = enforce_section_scope(_regroup(stories, template))
    report.extend(log)
    stories = _flatten(scoped)

    # 3. One item per event inside the edition.
    stories, log = group_within_edition(stories)
    report.extend(log)

    # 4. Leave out events already published in the last seven days unless something new happened.
    stories, log = drop_repeats(stories, ctx.store, ctx.today)
    report.extend(log)

    # 5. Summaries written from the primary article only and proven against it.
    for story in stories:
        source = str(story.get("page_text", "")) or (str(story.get("summary", "")) if story.get("origin") == "Federal Register API" else "")
        if ctx.write is not None and source:
            summary, record = ground_summary(str(story.get("title", "")), source, write=ctx.write, entail=ctx.entail,
                                             draft="")
        else:
            summary, record = str(story.get("summary", "")), GroundingRecord(method="feed-text")
        for failure in record.failures:
            report.add("summary-regenerated" if record.method != "extractive-fallback" else "summary-fallback",
                       str(story.get("title", "")), failure, str(story.get("url", "")), method=record.method)
        if record.method == "extractive-fallback":
            report.add("summary-fallback", str(story.get("title", "")), "used the article's first sentences after two failed attempts",
                       str(story.get("url", "")))
        story["summary"] = style_text(summary)
        story["summary_method"] = record.method
        ctx.source_texts[str(story.get("url", ""))] = source[:4000]
        story.pop("page_text", None)

    # 6. Scope again now that the summary is the grounded one.
    scoped, log = enforce_section_scope(_regroup(stories, template))
    report.extend(log)
    stories = _flatten(scoped)

    # 7. Executive Summary inputs: priority tier, and no repeat lead unless it moved.
    last_editions = sorted({e["edition"] for e in ctx.store if e["edition"] < ctx.today.isoformat()}, reverse=True)[:LEAD_LOOKBACK_EDITIONS]
    recent_leads = [e for e in ctx.store if e.get("led") and e["edition"] in last_editions]
    for story in stories:
        story["priority_tier"] = priority_tier(story)
        match = previous_match(story, recent_leads) if recent_leads else None
        story["lead_ok"] = match is None or bool(new_facts_since(story, match))
        if not story["lead_ok"]:
            report.add("lead-blocked", str(story.get("title", "")), f"led the {match['edition']} edition; cannot lead again without new facts",
                       str(story.get("url", "")))
    report.note("stories_after_quality_pass", len(stories))
    return _regroup(stories, template)
