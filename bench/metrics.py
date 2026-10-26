"""Deterministic checks on a summary (no model involved)."""

from __future__ import annotations

import re
from typing import Any

import validation_gates as gates
from bench import prompts

BARE = re.compile(r"\[(https?://[^\]\s]+)\]")
LINK = re.compile(r"\[([^\]]{1,200})\]\((https?://[^\s)]+)\)")


def check(text: str, allowed_urls: set[str]) -> dict[str, Any]:
    links = LINK.findall(text)
    plain = LINK.sub(lambda m: m.group(1), text)
    words = len(plain.split())
    paragraphs = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    lowered = plain.casefold()
    banned = [phrase for phrase in prompts.BANNED_PHRASES if re.search(r"\b" + re.escape(phrase) + r"\b", lowered)]
    workflow = [word for word in prompts.WORKFLOW_WORDS if word in lowered]
    template = [p.pattern for p in gates.TEMPLATE_PATTERNS if p.search(plain.strip())]
    bad_urls = [url for _, url in links if url not in allowed_urls]
    return {
        "words": words,
        "word_range_ok": 220 <= words <= 350,
        "paragraphs": len(paragraphs),
        "paragraphs_ok": 3 <= len(paragraphs) <= 4,
        "links": len(links),
        "bare_url_links": len(BARE.findall(text)),
        "links_ok": len(links) >= 3 and all(len(LINK.findall(p)) >= 1 for p in paragraphs),
        "invented_urls": bad_urls,
        "banned_phrases": banned,
        "workflow_words": workflow,
        "template_text": template,
        "passes_shape": (
            220 <= words <= 350 and 3 <= len(paragraphs) <= 4 and len(links) >= 3
            and not bad_urls and not banned and not workflow and not template
        ),
    }
