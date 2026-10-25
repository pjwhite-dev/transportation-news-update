"""Deterministic reader-facing article-summary quality rules."""

from __future__ import annotations

import html
import re
from typing import Any


_ABBREVIATIONS = (
    "U.S.", "U.K.", "D.C.", "e.g.", "i.e.", "Mr.", "Mrs.", "Ms.", "Dr.",
    "No.", "Inc.", "Corp.", "Co.", "Ltd.", "vs.",
)
_SENTENCE_END = re.compile(r"[.!?](?=(?:[\"'’”\)\]]*)?(?:\s|$))")


def sentence_count(value: str) -> int:
    """Count complete sentences while ignoring common dotted abbreviations."""
    text = " ".join(str(value).split())
    if not text:
        return 0
    protected = text
    for abbreviation in _ABBREVIATIONS:
        protected = re.sub(
            re.escape(abbreviation),
            abbreviation.replace(".", "\u2024"),
            protected,
            flags=re.IGNORECASE,
        )
    return len(_SENTENCE_END.findall(protected))


def has_one_or_two_sentences(value: str) -> bool:
    return sentence_count(value) in {1, 2}


def final_summary_qc_errors(
    briefing: dict[str, Any],
    *,
    html_text: str | None = None,
    plain_text: str | None = None,
) -> list[str]:
    errors: list[str] = []
    sections = briefing.get("sections", {})
    if not isinstance(sections, dict):
        return ["The briefing has no valid section map."]
    for section, items in sections.items():
        if not isinstance(items, list):
            errors.append(f"{section} is not a story list.")
            continue
        for index, item in enumerate(items, start=1):
            title = str(item.get("title", "Untitled")).strip()
            summary = str(item.get("summary", "")).strip()
            count = sentence_count(summary)
            if count not in {1, 2}:
                errors.append(
                    f"{section} story {index} ({title}) has {count} summary sentences; 1 or 2 required."
                )
                continue
            if html_text is not None and html.escape(summary) not in html_text:
                errors.append(f"{section} story {index} summary is missing from HTML.")
            if plain_text is not None and summary not in plain_text:
                errors.append(f"{section} story {index} summary is missing from plain text.")
    return errors
