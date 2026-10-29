"""The quality pass: runs between story assembly and the Executive Summary.

Each step is deterministic or a bounded local-model check, and each drop or rewrite is logged to the QA
report. Order matters: only stories that pass every step reach the Executive Summary.

    source labels -> section scope -> same-event groups -> repeats from earlier editions
    -> grounded summaries -> house style -> section scope again (topic not incidental)
    -> lead eligibility and priority -> edition cap
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from editorial_checks import angle_in_headline, enforce_section_scope, scope_fits, section_scopes, source_label, style_text
from grounding import GroundingRecord, ground_summary
from qa_report import QAReport
from story_groups import drop_repeats, group_within_edition, previous_match, new_facts_since

POLICY_SECTIONS = {"Federal Policy & Implementation", "Federal Register", "Surface Transportation Reauthorization"}
FOREIGN_MILITARY_SECTIONS = {"Military UAS", "International Security"}
FEDERAL_POLICY = "Federal Policy & Implementation"
LEAD_LOOKBACK_EDITIONS = 2
MAX_TOPIC_CHECKS = 30
# Owner approved 2026-10-29 after a 34-story replay: past 25 stories, cut the lowest-priority ones.
EDITION_STORY_CAP = 25
TOPIC_SYSTEM = """You decide whether a news story belongs in a section of an advanced-transportation news update.
Answer central=true when the section's subject is what the story is mainly about, including analysis, interviews
and opinion pieces on that subject. Answer central=false when the subject is only a passing mention in a story
that is mainly about something else (a war, a crime, an election, the economy, a sea mine, sabotage in general).
Respond with JSON only."""
TOPIC_SCHEMA = {
    "type": "object",
    "properties": {"central": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["central", "reason"],
    "additionalProperties": False,
}
POLICY_ACTION = re.compile(
    r"\b(proposed rule|proposes?|proposal|final rule|interim final|rulemaking|notice of|executive order|advisory circular|"
    r"guidance|regulations?|legislation|bill|act|approves?|approved|approval|grants?|funding|awards?|waiver|exemption|"
    r"standards?|directive|mandate|certif\w+|authoriz\w+|order)\b", re.I)
# A headline about a U.S. federal action, whoever reported it. 2026-10-29: the Trump robotics proclamation
# (unite.ai) and the FCC drone vote (a blog) ranked as ordinary industry news and Federal Policy was empty.
FEDERAL_ACTION = re.compile(
    r"\b(?:president|trump|white house|commerce (?:department|secretary)|department of (?:commerce|transportation)|"
    r"fcc|faa|nhtsa|fmcsa|fra|fta|phmsa|u\.?s\.? dot|congress|senate|house (?:passes|votes|committee))\b[^.:]{0,60}?"
    r"\b(?:proclamation|executive order|direct(?:s|ed)?|signs?|signed|votes?|voted|proposes?|proposed|finaliz\w+|adopts?|"
    r"issues?|issued|approves?|approved|authoriz\w+|passes|passed|bans?|requires?|(?:is )?(?:about|set|poised|moving) to|"
    r"plans? to|will (?:vote|allow|require|authoriz\w+))\b", re.I)
# Who acts, for the official-source search only ("FRA Announces $2.3B ..." is a federal action FEDERAL_ACTION misses).
FEDERAL_ACTOR = re.compile(
    r"\b(?:president|trump|white house|commerce department|department of (?:commerce|transportation|energy|homeland security)|"
    r"fcc|faa|nhtsa|fmcsa|fra|fta|phmsa|nasa|tsa|dhs|u\.?s\.? dot|usdot|congress|senate)\b", re.I)


@dataclass
class QualityContext:
    report: QAReport
    today: date
    store: list[dict[str, Any]] = field(default_factory=list)
    write: Callable[[str, str, str], str] | None = None
    entail: Callable[[str, list[str]], list[tuple[bool, str]]] | None = None
    same_event: Callable[[dict[str, Any], dict[str, Any]], tuple[bool, str]] | None = None
    source_texts: dict[str, str] = field(default_factory=dict)
    on_topic: Callable[[dict[str, Any], str], tuple[bool, str]] | None = None
    # url -> {"title", "text"} for a live U.S. government article page, or None.
    fetch_official: Callable[[str], dict[str, str] | None] | None = None
    official_records: list[dict[str, Any]] = field(default_factory=list)
    # (query, site) -> recent government article results [{"url", "title", "text"}] from the local search engine.
    search_official: Callable[[str, str], list[dict[str, str]]] | None = None


def make_topic_check(verifier: Any) -> Callable[[dict[str, Any], str], tuple[bool, str]]:
    """Wrap a local provider as on_topic(story, section) -> (central, reason)."""

    def on_topic(story: dict[str, Any], section: str) -> tuple[bool, str]:
        definition = section_scopes()[section]["definition"]
        result = verifier.generate_structured(
            [{"role": "system", "content": TOPIC_SYSTEM},
             {"role": "user", "content": f"SECTION\n{section}: {definition}\n\nSTORY\nHeadline: {story.get('title', '')}\n"
                                         f"Summary: {str(story.get('summary', ''))[:700]}"}],
            schema_name="topic_check", schema=TOPIC_SCHEMA, max_output_tokens=800,
        )
        return bool(result.value.get("central")), str(result.value.get("reason", ""))[:120]

    return on_topic


def topic_is_incidental(stories: list[dict[str, Any]], ctx: "QualityContext") -> list[dict[str, Any]]:
    """Drop stories whose headline lacks the topic and whose section subject the verifier finds incidental."""
    if ctx.on_topic is None:
        return stories
    kept: list[dict[str, Any]] = []
    asked = 0
    for story in stories:
        section = str(story.get("section", ""))
        if (story.get("required_include") is True or story.get("origin") == "Federal Register API"
                or section not in section_scopes() or angle_in_headline(story, section) or asked >= MAX_TOPIC_CHECKS):
            kept.append(story)
            continue
        asked += 1
        try:
            central, why = ctx.on_topic(story, section)
        except Exception:  # a verifier hiccup keeps the story, as before
            kept.append(story)
            continue
        if central:
            kept.append(story)
        else:
            ctx.report.add("scope", str(story.get("title", "")), f"dropped from {section}: the topic is incidental ({why})",
                           str(story.get("url", "")))
    return kept


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


def is_federal_action(story: dict[str, Any]) -> bool:
    """A U.S. federal action named in the headline (foreign and military sections excluded)."""
    return story.get("section") not in FOREIGN_MILITARY_SECTIONS and bool(FEDERAL_ACTION.search(str(story.get("title", ""))))


def priority_tier(story: dict[str, Any]) -> int:
    """1 federal policy actions, 2 company and industry news, 3 foreign and military news."""
    from story_groups import source_tier

    text = f"{story.get('title', '')} {story.get('summary', '')}"
    if story.get("origin") == "Federal Register API" or story.get("section") in POLICY_SECTIONS:
        return 1
    # A government source alone is not a policy action: a NASA flight-test photo story is not one.
    if source_tier(story) == 1 and POLICY_ACTION.search(text):
        return 1
    if is_federal_action(story):
        return 1
    if story.get("section") in FOREIGN_MILITARY_SECTIONS:
        return 3
    return 2


OFFICIAL_NAMES = {
    "whitehouse.gov": "The White House", "transportation.gov": "U.S. DOT", "faa.gov": "FAA", "nhtsa.gov": "NHTSA",
    "fmcsa.dot.gov": "FMCSA", "railroads.dot.gov": "FRA", "fra.dot.gov": "FRA", "transit.dot.gov": "FTA",
    "phmsa.dot.gov": "PHMSA", "fcc.gov": "FCC", "commerce.gov": "Commerce Department", "federalregister.gov": "Federal Register",
    "congress.gov": "Congress.gov", "nasa.gov": "NASA", "dhs.gov": "DHS", "tsa.gov": "TSA", "defense.gov": "Department of War",
    "war.gov": "Department of War", "senate.gov": "U.S. Senate", "house.gov": "U.S. House", "ntsb.gov": "NTSB",
}
MAX_OFFICIAL_CHECKS = 40
MAX_OFFICIAL_CANDIDATES = 5
MAX_OFFICIAL_SEARCHES = 10
# The agency named in a headline -> the site its own announcement lives on (first match wins).
AGENCY_SITES = (
    (re.compile(r"\b(?:president|trump|white house)\b", re.I), "whitehouse.gov"),
    (re.compile(r"\bfaa\b", re.I), "faa.gov"), (re.compile(r"\bnhtsa\b", re.I), "nhtsa.gov"),
    (re.compile(r"\bfmcsa\b", re.I), "fmcsa.dot.gov"), (re.compile(r"\bfra\b", re.I), "railroads.dot.gov"),
    (re.compile(r"\bfta\b", re.I), "transit.dot.gov"), (re.compile(r"\bphmsa\b", re.I), "phmsa.dot.gov"),
    (re.compile(r"\bfcc\b", re.I), "fcc.gov"), (re.compile(r"\bnasa\b", re.I), "nasa.gov"),
    (re.compile(r"\b(?:tsa)\b", re.I), "tsa.gov"), (re.compile(r"\b(?:dhs|homeland security)\b", re.I), "dhs.gov"),
    (re.compile(r"\bcommerce\b", re.I), "commerce.gov"),
    (re.compile(r"\b(?:u\.?s\.? dot|usdot|department of transportation)\b", re.I), "transportation.gov"),
    (re.compile(r"\b(?:congress|senate)\b", re.I), "congress.gov"),
)


def agency_site(title: str) -> str:
    return next((site for pattern, site in AGENCY_SITES if pattern.search(title)), "")


def official_name(url: str) -> str:
    from urllib.parse import urlparse

    host = urlparse(url).netloc.casefold().removeprefix("www.")
    for known, name in OFFICIAL_NAMES.items():
        if host == known or host.endswith("." + known):
            return name
    return source_label("", url)


def attach_official_sources(stories: list[dict[str, Any]], ctx: "QualityContext") -> None:
    """For tier-1 stories from the press: find the government page for the same action and link it first.

    Candidates are the .gov/.mil pages the article itself links to, then government records in today's pool
    that share a name or figure with the story. Each must be a live article page and the verifier must agree
    it reports the same action. When none qualifies, the agency's own site is searched (past week only);
    most agency sites refuse automated fetches, so a result the page fetch cannot read is judged on the
    search engine's title and snippet. Otherwise nothing is linked.
    """
    from article_extract import is_official_host
    from story_groups import key_terms

    if ctx.fetch_official is None or ctx.same_event is None:
        return
    checks = searches = 0

    def judge(story: dict[str, Any], candidate: str, page: dict[str, str]) -> bool:
        name = official_name(candidate)
        try:
            same, why = ctx.same_event(story, {"title": page.get("title", ""), "summary": page.get("text", "")[:600], "source": name})
        except Exception:  # a verifier hiccup links nothing
            return False
        if same:
            story["also_covered"] = [{"source": name, "url": candidate, "official": True}, *(story.get("also_covered") or [])]
            ctx.report.add("official-source", str(story.get("title", "")), f"linked {name}: {why}", candidate)
        return same

    for story in stories:
        url = str(story.get("url", ""))
        extras = list(story.get("also_covered") or [])
        reports_federal = story.get("priority_tier") == 1 or (
            story.get("section") not in FOREIGN_MILITARY_SECTIONS and FEDERAL_ACTOR.search(str(story.get("title", ""))))
        if (not reports_federal or is_official_host(url) or story.get("origin") == "Federal Register API"
                or any(isinstance(e, dict) and e.get("official") for e in extras)):
            continue
        terms = key_terms(story)
        pool = sorted(
            (r for r in ctx.official_records if str(r.get("url", "")) != url),
            key=lambda r: -len(terms & key_terms(r)),
        )
        candidates = list(dict.fromkeys(
            [str(u) for u in story.get("page_official_links") or []]
            + [str(r.get("url", "")) for r in pool[:3] if terms & key_terms(r)]
        ))[:MAX_OFFICIAL_CANDIDATES]
        linked = False
        for candidate in candidates:
            if checks >= MAX_OFFICIAL_CHECKS:
                return
            page = ctx.fetch_official(candidate)
            if not page:
                continue
            checks += 1
            if judge(story, candidate, page):
                linked = True
                break
        if linked or ctx.search_official is None or searches >= MAX_OFFICIAL_SEARCHES:
            continue
        searches += 1
        site = agency_site(str(story.get("title", ""))) or "gov"
        results: list[dict[str, str]] = []
        # Headlines abbreviate ("$2.3B"); agency releases spell out, so the summary's first sentence is the second try.
        for query in (str(story.get("title", "")), str(story.get("summary", "")).split(". ")[0]):
            try:
                results = ctx.search_official(query, site) if query.strip() else []
            except Exception:  # no search engine: nothing is linked, as before
                results = []
            if results:
                break
        for result in results[:3]:
            candidate = str(result.get("url", ""))
            if checks >= MAX_OFFICIAL_CHECKS:
                return
            if not is_official_host(candidate) or candidate == url:
                continue
            checks += 1
            if judge(story, candidate, ctx.fetch_official(candidate) or result):
                break


def priority_key(story: dict[str, Any]) -> tuple[int, int]:
    return (int(story.get("priority_tier", 2) or 2), -int(story.get("importance", 0) or 0))


def cap_edition(stories: list[dict[str, Any]], report: QAReport, cap: int = EDITION_STORY_CAP) -> list[dict[str, Any]]:
    """Keep at most `cap` stories: editor-vetted items, then each section's best, then by priority."""
    if len(stories) <= cap:
        return stories
    ranked = sorted(stories, key=priority_key)  # stable: ties keep the edition's order
    keep: list[int] = [id(s) for s in ranked if s.get("required_include") is True]
    for section in dict.fromkeys(str(s.get("section", "")) for s in ranked):
        best = next(s for s in ranked if str(s.get("section", "")) == section)
        if id(best) not in keep:
            keep.append(id(best))
    for story in ranked:
        if len(keep) >= cap:
            break
        if id(story) not in keep:
            keep.append(id(story))
    kept_ids = set(keep)
    for story in ranked:
        if id(story) not in kept_ids:
            report.add("edition-cap", str(story.get("title", "")),
                       f"cut to keep the edition to {cap} stories (priority tier {story.get('priority_tier')}, importance {story.get('importance')})",
                       str(story.get("url", "")))
    return [s for s in stories if id(s) in kept_ids]


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
    stories, log = group_within_edition(stories, judge=ctx.same_event)
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
    stories = topic_is_incidental(_flatten(scoped), ctx)

    # 7. Executive Summary inputs: priority tier, and no repeat lead unless it moved.
    last_editions = sorted({e["edition"] for e in ctx.store if e["edition"] < ctx.today.isoformat()}, reverse=True)[:LEAD_LOOKBACK_EDITIONS]
    recent_leads = [e for e in ctx.store if e.get("led") and e["edition"] in last_editions]
    for story in stories:
        if (is_federal_action(story) and story.get("section") not in POLICY_SECTIONS and FEDERAL_POLICY in template
                and scope_fits(story, FEDERAL_POLICY)):
            report.add("scope", str(story.get("title", "")), f"moved from {story.get('section')} to {FEDERAL_POLICY}: federal action in the headline",
                       str(story.get("url", "")))
            story["section"] = FEDERAL_POLICY
        story["priority_tier"] = priority_tier(story)
        match = previous_match(story, recent_leads) if recent_leads else None
        story["lead_ok"] = match is None or bool(new_facts_since(story, match))
        if not story["lead_ok"]:
            report.add("lead-blocked", str(story.get("title", "")), f"led the {match['edition']} edition; cannot lead again without new facts",
                       str(story.get("url", "")))

    # 8. A readable edition: past the cap, the lowest-priority stories go (each section keeps its best one).
    stories = cap_edition(stories, report)

    # 9. A federal action reported by the press links the government's own page for it, when one is found.
    attach_official_sources(stories, ctx)
    for story in stories:
        story.pop("page_official_links", None)
    report.note("stories_after_quality_pass", len(stories))
    return _regroup(stories, template)
