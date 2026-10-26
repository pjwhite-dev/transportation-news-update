"""Evidence-only Executive Summary chain (Phase 2; replaces the single free-text pass).

1. The writer (local qwen3.8) sees a numbered fact sheet and must end every sentence with the id(s)
   of the stories that state its facts, e.g. "The FAA cleared Beta for demo flights [S4]."
2. Code turns the ids into numbered links to the real story URLs. A model can never invent a link,
   because it never writes a URL.
3. A second local model from a different family (gemma4) checks every sentence against the fact sheet.
4. Problem sentences go back to the writer to fix (at most twice). Whatever is still flagged is removed
   by code, never published. If too little is left, the run fails and alerts the owner.

Owner override: local models only, no paid APIs. Benchmark: docs/benchmark-2026-10-26.md.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from ai_providers import AIProvider, OllamaProvider

FACT_SHEET_SIZE = 25
MAX_REVISIONS = 2
MIN_WORDS_AFTER_REPAIR = 100
MIN_CITATIONS = 3
DEFAULT_VERIFIER_MODEL = "gemma4:31b"

BANNED_PHRASES = (
    "landmark", "game-changer", "game changer", "poised to", "underscores", "amid",
    "navigating the landscape", "it remains to be seen", "source headline", "watch for follow-up developments",
)

WRITER_SYSTEM = f"""You write the Executive Summary of a daily U.S. advanced-transportation news update read by senior executives (drones, counter-drone, eVTOL, autonomous vehicles, rail, civil supersonic, defense technology).

You are given a FACT SHEET of the day's stories. Each story has an id such as S3.

Rules, in order of importance:
1. Every sentence states only facts that appear in the fact sheet, and ends with the id(s) of the story or stories that state those facts, like: The FAA cleared Beta Technologies for passenger demonstration flights [S4]. Never cite a story that does not state the fact. Never add a fact, number, date, name, cause or consequence the sheet does not contain.
2. Do not interpret. No sentence may say what something signals, means, reflects, shows, suggests, supports, or aligns with, and none may describe a trend, unless a story states exactly that. Do not add background or context from your own knowledge.
3. Verbs must match what actually happened: "issued", "finalized", "approved" only for completed actions; use "proposed", "announced plans to", "tested", "demonstrated", "said" for the rest. Never describe a proposal or plan as a finished action.
4. Write 3 short paragraphs (4 at most), 160 to 260 words in total. The first sentence names the single most consequential development and the consequence its story states.
5. Active voice. Name an agency in full on first reference. Use numerals. No lists of headlines. No process language (never mention a feed, records, stories, sections or how this was assembled).
6. Never use: {", ".join(BANNED_PHRASES[:8])}.
Output only the summary text. Cite with ids in square brackets, [S3] or [S3][S5]. No URLs and no other brackets."""

VERIFIER_SYSTEM = """You are a fact checker. You are given a FACT SHEET and a numbered list of sentences from a news summary. For EACH sentence decide:
- "supported": every factual claim in the sentence is stated in the fact sheet (names, numbers, dates, actions) and the cited ids are stories that state it.
- "unsupported": the sentence contains a claim, number, date, cause, consequence or interpretation that the fact sheet does not state (including conclusions such as what something signals or reflects).
- "status_error": the facts are in the sheet but the sentence misstates what kind of action it was (for example calls a proposal, plan, test or announcement a completed rule, approval or operation).
Be strict. Respond with JSON only."""

VERIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["supported", "unsupported", "status_error"]},
                    "note": {"type": "string"},
                },
                "required": ["n", "verdict", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["sentences"],
    "additionalProperties": False,
}

_MARKER_GROUP = re.compile(r"(?:\s*\[\s*S\d+(?:\s*,\s*S\d+)*\s*\])+")
_MARKER_ID = re.compile(r"S\d+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])(?:\s*\[[^\]]*\])*\s+(?=[A-Z\"'(\[])")


@dataclass
class ChainResult:
    text: str  # markdown with numbered [n](url) citation links, paragraphs separated by a blank line
    usage: dict[str, int] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)


class SummaryChainError(RuntimeError):
    """The chain could not produce a summary that survived verification."""


def build_fact_sheet(sections: dict[str, list[dict[str, Any]]]) -> list[dict[str, str]]:
    """Top stories by importance (ties keep section order) with stable ids S1..Sn."""
    stories = []
    for section, items in sections.items():
        for item in items:
            if str(item.get("url", "")).startswith(("http://", "https://")) and item.get("title"):
                stories.append((section, item))
    stories.sort(key=lambda pair: -int(pair[1].get("importance", 0) or 0))
    from public_site import display_title

    sheet = []
    for index, (section, item) in enumerate(stories[:FACT_SHEET_SIZE], start=1):
        sheet.append(
            {
                "id": f"S{index}",
                "section": str(section),
                "headline": display_title(item),
                "source": str(item.get("source", "")),
                "date": str(item.get("date_label", "")),
                "summary": " ".join(str(item.get("summary", "")).split()),
                "url": str(item["url"]),
            }
        )
    return sheet


def fact_sheet_text(sheet: list[dict[str, str]]) -> str:
    lines = []
    for story in sheet:
        lines.append(
            f"[{story['id']}] ({story['section']}) {story['headline']}\n"
            f"    source: {story['source']} {story['date']}\n"
            f"    summary: {story['summary'] or '(none)'}"
        )
    return "\n".join(lines)


def split_sentences(paragraph: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_SPLIT.split(" ".join(paragraph.split())) if part.strip()]


def strip_markers(text: str) -> str:
    return " ".join(_MARKER_GROUP.sub("", text).split())


def cited_ids(sentence: str) -> list[str]:
    ids: list[str] = []
    for group in _MARKER_GROUP.findall(sentence):
        ids.extend(_MARKER_ID.findall(group))
    return ids


def link_citations(text: str, sheet: list[dict[str, str]]) -> str:
    """Replace [S3] markers with numbered links to the real URLs (numbered by first use)."""
    urls = {story["id"]: story["url"] for story in sheet}
    numbers: dict[str, int] = {}

    def convert(match: re.Match[str]) -> str:
        links = []
        for story_id in _MARKER_ID.findall(match.group(0)):
            if story_id not in urls:
                continue  # unknown id: dropped (the sentence then fails the "cited" check)
            numbers.setdefault(story_id, len(numbers) + 1)
            links.append(f"[{numbers[story_id]}]({urls[story_id]})")
        return (" " + "".join(links)) if links else ""

    return _MARKER_GROUP.sub(convert, text)


def _paragraphs(raw: str) -> list[str]:
    return [" ".join(block.split()) for block in re.split(r"\n\s*\n", raw.strip()) if block.strip()]


def audit_sentences(paragraphs: list[str], sheet: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Code-side checks per sentence: must cite at least one known story."""
    known = {story["id"] for story in sheet}
    rows = []
    for p_index, paragraph in enumerate(paragraphs):
        for sentence in split_sentences(paragraph):
            ids = cited_ids(sentence)
            rows.append(
                {
                    "paragraph": p_index,
                    "raw": sentence,
                    "plain": strip_markers(sentence),
                    "ids": ids,
                    "cited": bool(ids) and all(i in known for i in ids),
                }
            )
    return rows


def verify(verifier: AIProvider, sheet: list[dict[str, str]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    numbered = "\n".join(f"{i}. {row['plain']}  (cites: {', '.join(row['ids']) or 'none'})" for i, row in enumerate(rows, 1))
    body = f"FACT SHEET\n\n{fact_sheet_text(sheet)}\n\nSENTENCES TO CHECK\n{numbered}"
    result = verifier.generate_structured(
        [{"role": "system", "content": VERIFIER_SYSTEM}, {"role": "user", "content": body}],
        schema_name="summary_support", schema=VERIFIER_SCHEMA, max_output_tokens=12000,
    )
    verdicts = {item["n"]: item for item in result.value["sentences"]}
    for index, row in enumerate(rows, start=1):
        item = verdicts.get(index)
        # A sentence the verifier did not answer is treated as unsupported.
        row["verdict"] = item["verdict"] if item else "unsupported"
        row["note"] = item["note"] if item else "not checked"
        if not row["cited"]:
            row["verdict"] = "unsupported"
            row["note"] = (row["note"] + " | no valid citation").strip(" |")
    return result.usage


def _problem_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row["verdict"] != "supported"]


def _render(paragraphs_rows: list[list[dict[str, Any]]], sheet: list[dict[str, str]]) -> str:
    paragraphs = [" ".join(row["raw"] for row in rows) for rows in paragraphs_rows if rows]
    return link_citations("\n\n".join(paragraphs), sheet)


def _by_paragraph(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["paragraph"], []).append(row)
    return [grouped[key] for key in sorted(grouped)]


def _feedback(problems: list[dict[str, Any]]) -> str:
    lines = ["A fact checker flagged these sentences. Rewrite the summary without them, or restate them so "
             "that they say only what the cited story states. Keep every other rule."]
    for row in problems:
        lines.append(f'- "{row["plain"]}" -> {row["verdict"]}: {row["note"]}')
    return "\n".join(lines)


def run_chain(
    writer: AIProvider,
    verifier: AIProvider,
    sections: dict[str, list[dict[str, Any]]],
) -> ChainResult:
    sheet = build_fact_sheet(sections)
    if len(sheet) < 3:
        raise SummaryChainError("Fewer than three citable stories; cannot write a verified summary.")
    base = [
        {"role": "system", "content": WRITER_SYSTEM},
        {"role": "user", "content": "FACT SHEET\n\n" + fact_sheet_text(sheet) + "\n\nWrite the Executive Summary now."},
    ]
    usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def add(extra: dict[str, int]) -> None:
        for key in usage:
            usage[key] += int(extra.get(key, 0) or 0)

    messages = list(base)
    rounds: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    started = time.monotonic()
    for attempt in range(MAX_REVISIONS + 1):
        draft = writer.generate_text(messages, max_output_tokens=1500)
        add(draft.usage)
        paragraphs = _paragraphs(draft.text)
        rows = audit_sentences(paragraphs, sheet)
        if not rows:
            raise SummaryChainError("The writer returned no sentences.")
        add(verify(verifier, sheet, rows))
        problems = _problem_rows(rows)
        rounds.append({"attempt": attempt, "sentences": len(rows), "flagged": len(problems),
                       "status_errors": sum(r["verdict"] == "status_error" for r in rows)})
        if not problems:
            break
        messages = base + [
            {"role": "assistant", "content": draft.text},
            {"role": "user", "content": _feedback(problems)},
        ]

    dropped = 0
    if _problem_rows(rows):
        # Final safety: unverified sentences are removed by code; they are never published.
        dropped = len(_problem_rows(rows))
        rows = [row for row in rows if row["verdict"] == "supported"]
    text = _render(_by_paragraph(rows), sheet)
    plain_words = len(re.sub(r"\[\d+\]\([^)]*\)", "", text).split())
    citations = len(re.findall(r"\[\d+\]\(https?://", text))
    if plain_words < MIN_WORDS_AFTER_REPAIR or citations < MIN_CITATIONS:
        raise SummaryChainError(
            f"Only {plain_words} verified words and {citations} citations remained after checking; refusing to publish."
        )
    return ChainResult(
        text=text,
        usage=usage,
        verification={
            "verified_by": getattr(verifier, "model", ""),
            "writer": getattr(writer, "model", ""),
            "rounds": rounds,
            "sentences_dropped": dropped,
            "unsupported_remaining": 0,
            "status_errors_remaining": 0,
            "seconds": round(time.monotonic() - started, 1),
        },
    )


def verifier_from_env(environ: dict[str, str] | None = None) -> OllamaProvider:
    env = os.environ if environ is None else environ
    return OllamaProvider(
        base_url=env.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        model=env.get("OLLAMA_VERIFIER_MODEL", DEFAULT_VERIFIER_MODEL),
        think=True,
        temperature=0.1,
        num_ctx=32768,
        stall_timeout_seconds=300,
        timeout_seconds=900,
        max_attempts=2,
    )
