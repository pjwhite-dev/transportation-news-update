"""Build a blind comparison packet to paste into Claude and/or ChatGPT, and score their answers.

    python -m bench.external_packet            # writes bench/data/external/packet_part1.md, _part2.md, packet_full.md
    python -m bench.external_packet --score bench/data/external/answer_claude_part1.json ...   # decode answers

Candidates per day (labels shuffled per day, model names removed, link markup stripped):
the summary published that day, qwen3.6, qwen3.8, gemma4 (raw benchmark outputs), and "chain"
(qwen3.8 writing from cited stories, checked sentence by sentence by gemma4).
The key that maps labels back to candidates is stored in key.json and is NOT part of the packet.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from statistics import mean

import summary_chain as chain
from bench import common
from bench.chain_replay import REPLAY_DAYS

OUT = common.DATA / "external"
LINK = re.compile(r"\[([^\]]{1,200})\]\((https?://[^\s)]+)\)")
BARE = re.compile(r"\s*\[https?://[^\]\s]+\]")
NUMERIC_CITE = re.compile(r"\s*\[\d+\]\(https?://[^)]*\)")


def plain(text: str) -> str:
    text = NUMERIC_CITE.sub("", text)
    return BARE.sub("", LINK.sub(lambda m: m.group(1), text)).strip()


INSTRUCTIONS = """# Blind comparison: which news summary is the most accurate, and which reads best?

You are an exacting news editor and fact-checker. Below are {days} separate "days". For each day you get:
- a FACT SHEET: the stories (id, headline, source, date, a short summary) that ALL of that day's summaries were written from, and
- {n} anonymous EXECUTIVE SUMMARIES (labelled A, B, C, ...) of that day's news. Labels are shuffled independently for each day, so the same letter on two days is unrelated.
The summaries come from different writers (people and AI systems). Do not try to guess which is which. Judge only what is on the page.
Do not browse the web or use any tools, and do not look anything up: the pages below are everything you need.

## How to judge accuracy (most important)
- The FACT SHEET is the ONLY evidence. Do not use outside knowledge, and do not "correct" the sheet. A claim that the sheet does not state is UNSUPPORTED even if it is true in the real world.
- Count a sentence as UNSUPPORTED if it contains any number, date, name, cause, consequence, quote or fact not stated in the sheet.
- Interpretation counts as UNSUPPORTED unless the sheet itself says it: for example "this signals...", "reflects a broader trend", "aligns with a push to...", "reshaping the landscape", or a trend claim not backed by at least two stories.
- Count a STATUS ERROR when the facts are in the sheet but the type of action is misstated: a proposal, plan, test, announcement or pending decision described as a completed rule, approval, award, launch or operation (or the reverse).
- Missing a minor story is NOT an accuracy error. Omitting the most consequential developments is a completeness weakness, not a factual error.

## How to judge readability (separately from accuracy)
Clarity, a strong first sentence that leads with the most consequential development, logical flow, plain active language, concision, no hype or filler, no headline lists, no process language ("the feed", "the stories below"). Ignore formatting and length unless they hurt readability.

## What to do for each day
1. For each summary, list the UNSUPPORTED and STATUS-ERROR claims as short quotes (about 12 words each), with the reason in a few words. Be thorough and strict.
2. Rank the summaries by ACCURACY, best first (fewest unsupported claims and status errors; break ties by how well it covers the most consequential stories).
3. Rank the summaries by READABILITY, best first, independent of accuracy.
4. Name the single best summary OVERALL (accuracy weighs about twice as much as readability).
Keep your per-day analysis concise so the whole answer fits.

## Output format
First write your per-day analysis. Then end with ONE JSON block and nothing after it, exactly in this shape (use the day numbers given; labels are the letters shown; every ranking lists every label for that day):
```json
{{"days": [
  {{"day": 1,
    "unsupported_counts": {{"A": 0, "B": 3}},
    "status_errors": {{"A": 0, "B": 1}},
    "accuracy_ranking": ["A", "B"],
    "readability_ranking": ["B", "A"],
    "best_overall": "A",
    "notes": "one short sentence"}}
]}}
```
"""


def candidates(day: str) -> dict[str, str] | None:
    chain_record = common.read_json(common.DATA / "chain" / f"{day}.json")
    if not chain_record or not chain_record.get("ok"):
        return None
    out = {
        "published": common.published_summary(day),
        "chain": chain_record["text"],
    }
    for name in ("qwen3.6", "qwen3.8", "gemma4"):
        record = common.read_json(common.DATA / "gen" / name / f"{day}.json")
        if record and record.get("text"):
            out[name] = record["text"]
    return out if len(out) == 5 else None


def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    key: dict[str, dict] = {}
    blocks: list[tuple[str, str]] = []
    days = [d for d in REPLAY_DAYS if candidates(d)]
    for number, day in enumerate(days, start=1):
        cands = candidates(day)
        rng = random.Random(f"external-{day}")
        names = sorted(cands)
        rng.shuffle(names)
        labels = "ABCDE"[: len(names)]
        key[str(number)] = {"day": day, "labels": dict(zip(labels, names))}
        sheet = chain.build_fact_sheet(common.load_edition(day)["sections"])
        lines = [f"\n\n---\n\n# DAY {number}\n", "## FACT SHEET\n"]
        for story in sheet:
            lines.append(f"[{story['id']}] {story['headline']} ({story['source']}, {story['date']})\n    {story['summary'] or '(no summary)'}")
        lines.append("\n## SUMMARIES\n")
        for label, name in zip(labels, names):
            lines.append(f"### Summary {label}\n{plain(cands[name])}\n")
        blocks.append((f"day{number}", "\n".join(lines)))
    common.write_json(OUT / "key.json", key)

    def packet(selected: list[tuple[str, str]], title: str) -> str:
        head = INSTRUCTIONS.format(days=len(selected), n=len(next(iter(key.values()))["labels"]))
        return head + "".join(block for _, block in selected) + f"\n\n---\n\nRemember: finish with the single JSON block. ({title})\n"

    half = (len(blocks) + 1) // 2
    parts = {"packet_full.md": blocks, "packet_part1.md": blocks[:half], "packet_part2.md": blocks[half:]}
    for filename, selected in parts.items():
        if not selected:
            continue
        # Part files keep the original day numbers so answers can be decoded with one key.
        text = packet(selected, filename)
        (OUT / filename).write_text(text, encoding="utf-8")
        print(filename, len(text.split()), "words,", len(selected), "days")


def score(files: list[str]) -> None:
    key = common.read_json(OUT / "key.json")
    tally: dict[str, dict[str, list]] = {}

    def add(name: str, field: str, value) -> None:
        tally.setdefault(name, {}).setdefault(field, []).append(value)

    for path in files:
        text = open(path, encoding="utf-8").read()
        match = re.search(r"\{.*\}", text, re.S)
        data = json.loads(match.group(0)) if match else {}
        for entry in data.get("days", []):
            mapping = key[str(entry["day"])]["labels"]
            for place, label in enumerate(entry["accuracy_ranking"], start=1):
                add(mapping[label], "accuracy_rank", place)
            for place, label in enumerate(entry["readability_ranking"], start=1):
                add(mapping[label], "readability_rank", place)
            for label, count in entry["unsupported_counts"].items():
                add(mapping[label], "unsupported", count)
            for label, count in entry["status_errors"].items():
                add(mapping[label], "status_errors", count)
            add(mapping[entry["best_overall"]], "best_overall", 1)
    print(f"{'candidate':10} {'days':>4} {'acc.rank':>9} {'read.rank':>10} {'unsupported/day':>16} {'status err/day':>15} {'best overall':>13}")
    for name, fields in sorted(tally.items(), key=lambda kv: mean(kv[1]["accuracy_rank"])):
        days = len(fields["accuracy_rank"])
        print(f"{name:10} {days:>4} {mean(fields['accuracy_rank']):>9.2f} {mean(fields['readability_rank']):>10.2f} "
              f"{mean(fields['unsupported']):>16.2f} {mean(fields['status_errors']):>15.2f} {len(fields.get('best_overall', [])):>13}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--score", nargs="*")
    args = parser.parse_args()
    score(args.score) if args.score is not None else build()


if __name__ == "__main__":
    main()
