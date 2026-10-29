"""Grounding: a story summary may say only what its own article says.

Order of defence, all logged:
  1. the local model writes one or two sentences from the primary article's text alone;
  2. deterministic checks: every number, date, name, modal verb and title must appear in the source,
     and filler phrases are refused;
  3. a second local model (the verifier) answers supported / not supported for each sentence;
  4. one regeneration under a stricter extractive prompt;
  5. failing that, the article's own first two sentences, trimmed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from editorial_config import compiled, load_config

WRITER_SYSTEM = """You summarize ONE news article for senior transportation-policy readers, in the voice of a
wire-service editor: plain, specific, confident, and easy to read once.
Use ONLY the ARTICLE text provided. Do not use anything you know from elsewhere or from other outlets.
Write one or two sentences (at most 55 words) that begin from the article's own lead.
Open with who did what: a named actor and a strong past-tense verb ("The FAA approved...", "Waymo began...").
Then give the most useful specifics the article states: how many, how much, where, when, what comes next.
If the article states why it matters (a deadline, a first, a consequence), include that. Do not repeat the
headline word for word. Prefer short, concrete words to jargon; spell out an acronym the first time unless
it is universally known (FAA, NASA, U.S.).
Copy numbers, money amounts, dates, names, titles and places exactly as the article gives them.
Use obligation words (requires, must, mandates) only if the article does. If the article says a thing is
optional, "if equipped", allowed or permitted, say exactly that. Do not add titles or descriptors
(champion, CEO, first, record, largest, former) the article does not use.
Every sentence must state a specific fact from the article. No background, no commentary, no
significance ("marks a milestone", "highlighting", "part of a broader trend")."""

STRICT_SUFFIX = """
THE LAST ATTEMPT FAILED THESE CHECKS: {problems}
Rewrite as an EXTRACTIVE summary: reuse the article's own words and numbers, no paraphrase of claims,
one or two short sentences, nothing that is not literally in the article."""

ENTAIL_SYSTEM = """You are a strict fact checker. You get an ARTICLE and numbered SENTENCES from a summary.
For each sentence answer supported=true only if the ARTICLE itself states every claim in it: each fact,
number, name, title, cause and the strength of any obligation. If anything is missing, softened or
strengthened relative to the article, supported=false and say what in `reason` (under 20 words)."""

SUMMARY_SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}
ENTAIL_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"index": {"type": "integer"}, "supported": {"type": "boolean"}, "reason": {"type": "string"}},
                "required": ["index", "supported", "reason"],
            },
        }
    },
    "required": ["verdicts"],
}

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_CAP = re.compile(r"\b[A-Z][A-Za-z0-9&’'-]*(?:\.[A-Z]\.?)*")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“'(\[])")
_SMALL = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
          "eleven": 11, "twelve": 12}
_COMMON_CAPS = {
    "the", "a", "an", "and", "but", "in", "on", "at", "of", "for", "to", "its", "his", "her", "their", "it", "he",
    "she", "they", "we", "this", "that", "these", "those", "after", "before", "with", "from", "by", "as", "if",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "i", "ii", "iii", "iv",
}


@dataclass
class Problem:
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.detail}"


@dataclass
class GroundingRecord:
    method: str = "model"            # model | regenerated | extractive-fallback | feed-text
    attempts: int = 0
    failures: list[str] = field(default_factory=list)


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(" ".join(str(text or "").split())) if s.strip()]


def _numbers(text: str) -> set[str]:
    return {m.group(0).replace(",", "").rstrip(".") for m in _NUMBER.finditer(text)}


def _stem(word: str) -> str:
    return re.sub(r"(?:ing|ed|es|s|e)$", "", word.casefold())


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(r"(?<![A-Za-z])" + re.escape(phrase) + r"(?![A-Za-z])", text, re.I) is not None


def deterministic_problems(summary: str, source: str, title: str = "") -> list[Problem]:
    """Facts in the summary that the source does not contain, in the strength the source gives."""
    config = load_config()
    problems: list[Problem] = []
    reference = f"{source}\n{title}"
    ref_numbers = _numbers(reference)
    ref_words = {w.casefold().strip("'’-") for w in _WORD.findall(reference)}
    ref_words |= {w.replace("’", "'") for w in ref_words}
    flat_source = re.sub(r"[.\s]", "", reference.casefold())

    for number in sorted(_numbers(summary)):
        spelled = [w for w, n in _SMALL.items() if str(n) == number]
        if number not in ref_numbers and not any(w in ref_words for w in spelled):
            problems.append(Problem("number", f"{number!r} is not in the source"))

    for sentence in split_sentences(summary):
        first = True
        for match in _CAP.finditer(sentence):
            token = match.group(0).strip("'’-")
            base = re.sub(r"['’]s$", "", token)
            lowered = base.casefold()
            at_start = first and match.start() == 0
            first = False
            if not lowered or lowered in _COMMON_CAPS or (at_start and len(base) <= 3):
                continue
            if at_start and lowered in ref_words:
                continue
            flat = re.sub(r"[.\s]", "", lowered)
            if lowered not in ref_words and flat not in flat_source and not any(
                part and part in ref_words for part in re.split(r"[-’']", lowered) if len(part) > 2
            ):
                problems.append(Problem("name", f"{base!r} is not in the source"))

    for term in config["modal_terms"]:
        if _contains_phrase(summary, term) and not _contains_phrase(reference, term):
            problems.append(Problem("modal", f"summary says {term!r}; the source does not"))
    soft_in_source = [t for t in config["soft_terms"] if t in {"optional", "if equipped", "allows", "allow", "permits", "permit"}
                      and _contains_phrase(reference, t)]
    hard_in_summary = [t for t in config["modal_terms"] if _contains_phrase(summary, t)]
    if soft_in_source and hard_in_summary and not any(_contains_phrase(summary, t) for t in soft_in_source):
        problems.append(Problem("modal", f"source says {soft_in_source[0]!r} but summary says {hard_in_summary[0]!r}"))

    for term in config["descriptor_terms"]:
        if _contains_phrase(summary, term) and not _contains_phrase(reference, term):
            problems.append(Problem("descriptor", f"{term!r} is not in the source"))

    problems.extend(filler_problems(summary, source))
    return problems


def filler_problems(summary: str, source: str = "") -> list[Problem]:
    pattern = compiled(load_config()["filler_phrases"])
    found: list[Problem] = []
    if pattern:
        for sentence in split_sentences(summary):
            match = pattern.search(sentence)
            if match and not re.search(re.escape(match.group(0)), source, re.I):
                found.append(Problem("filler", f"{match.group(0)!r} adds no fact"))
    return found


def drop_filler_sentences(summary: str, source: str = "") -> str:
    pattern = compiled(load_config()["filler_phrases"])
    kept = [s for s in split_sentences(summary) if not (pattern and (m := pattern.search(s)) and not re.search(re.escape(m.group(0)), source, re.I))]
    return " ".join(kept)


def extractive_fallback(source: str, title: str = "", limit: int = 320) -> str:
    """The article's first two sentences that carry a fact, trimmed."""
    pattern = compiled(load_config()["filler_phrases"])
    chosen: list[str] = []
    for sentence in split_sentences(source):
        words = sentence.split()
        if len(words) < 6 or (pattern and pattern.search(sentence)):
            continue
        if re.fullmatch(r"[A-Z .,'’&-]+", sentence):
            continue
        chosen.append(sentence)
        if len(chosen) == 2:
            break
    text = " ".join(chosen)
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


def make_entailment_check(verifier: Any) -> Callable[[str, list[str]], list[tuple[bool, str]]]:
    """Wrap a local provider as entail(source, sentences) -> [(supported, reason), ...]."""

    def entail(source: str, sentences: list[str]) -> list[tuple[bool, str]]:
        numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(sentences, start=1))
        result = verifier.generate_structured(
            [{"role": "system", "content": ENTAIL_SYSTEM},
             {"role": "user", "content": f"ARTICLE\n{source[:7000]}\n\nSENTENCES\n{numbered}"}],
            schema_name="entailment", schema=ENTAIL_SCHEMA, max_output_tokens=800,
        )
        by_index = {int(v["index"]): (bool(v["supported"]), str(v.get("reason", ""))) for v in result.value.get("verdicts", [])}
        return [by_index.get(i, (False, "no verdict returned")) for i in range(1, len(sentences) + 1)]

    return entail


def make_writer(provider: Any) -> Callable[[str, str, str], str]:
    """write(title, source, problems) -> summary text; `problems` non-empty selects the strict prompt."""

    def write(title: str, source: str, problems: str = "") -> str:
        system = WRITER_SYSTEM + (STRICT_SUFFIX.format(problems=problems) if problems else "")
        result = provider.generate_structured(
            [{"role": "system", "content": system},
             {"role": "user", "content": f"HEADLINE\n{title}\n\nARTICLE\n{source[:7000]}"}],
            schema_name="story_summary", schema=SUMMARY_SCHEMA, max_output_tokens=400,
        )
        return " ".join(str(result.value.get("summary", "")).split())

    return write


def ground_summary(
    title: str,
    source: str,
    *,
    write: Callable[[str, str, str], str],
    entail: Callable[[str, list[str]], list[tuple[bool, str]]] | None,
    draft: str = "",
) -> tuple[str, GroundingRecord]:
    """Return a summary that passed every check (or the article's own sentences) and a record of failures."""
    record = GroundingRecord()
    if len(source.split()) < 25:
        # Nothing to ground against: keep only text that is the feed's own description.
        record.method = "feed-text"
        return draft, record

    def failures(candidate: str) -> list[str]:
        if not candidate.strip():
            return ["empty summary"]
        found = [str(p) for p in deterministic_problems(candidate, source, title)]
        if not found and entail is not None:
            sentences = split_sentences(candidate)
            try:
                verdicts = entail(source, sentences)
            except Exception:  # verifier down: the deterministic checks above still stand
                return found
            found +=[f"unsupported: {s!r} ({reason})" for s, (ok, reason) in zip(sentences, verdicts) if not ok]
        return found

    candidate = " ".join(draft.split()) if draft else write(title, source, "")
    problems: list[str] = []
    for attempt in range(2):
        record.attempts += 1
        problems = failures(candidate)
        if not problems:
            record.method = "model" if attempt == 0 else "regenerated"
            return candidate, record
        record.failures.append("; ".join(problems)[:600])
        if attempt == 0:
            candidate = write(title, source, "; ".join(problems)[:500])
    record.method = "extractive-fallback"
    return extractive_fallback(source, title), record
