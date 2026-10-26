"""Aggregate the benchmark into bench/data/report.md and print it.

Picking rule (plan 7.10): the owner's win rate against the published summary first, then
claim-support rate, status errors, cost (always zero here, all local) and latency.
"""

from __future__ import annotations

from statistics import mean

from bench import common, metrics
from bench.judge import candidates_for

NAMES = list(common.MODELS)


def pct(value: float) -> str:
    return f"{100 * value:.0f}%"


def deterministic() -> dict[str, dict]:
    out = {}
    for name in NAMES:
        rows = []
        for day in common.BENCH_DAYS:
            record = common.read_json(common.DATA / "gen" / name / f"{day}.json")
            if not record:
                continue
            allowed = {s["url"] for s in common.fact_sheet(day)}
            rows.append((record, metrics.check(record["text"], allowed)))
        if not rows:
            continue
        out[name] = {
            "days": len(rows),
            "shape_pass": mean(m["passes_shape"] for _, m in rows),
            "words": mean(m["words"] for _, m in rows),
            "markdown_links": mean(m["links"] for _, m in rows),
            "bare_url_links": mean(m["bare_url_links"] for _, m in rows),
            "invented_urls": sum(len(m["invented_urls"]) for _, m in rows),
            "banned": sum(len(m["banned_phrases"]) for _, m in rows),
            "seconds": mean(r["seconds"] for r, _ in rows),
            "tok_per_s": mean(
                r["usage"].get("output_tokens", 0) / r["seconds"] for r, _ in rows if r["seconds"]
            ),
        }
    return out


def ranks_from(judgments: dict[str, dict], key: str) -> dict[str, list[int]]:
    """candidate -> list of rank positions (1 = best) over the days judged."""
    positions: dict[str, list[int]] = {}
    for day, entry in judgments.items():
        order = entry["ranking"] if key == "ranking" else None
        if order is None:
            continue
        for place, name in enumerate(order, start=1):
            positions.setdefault(name, []).append(place)
    return positions


def win_rate(judgments: dict[str, dict], name: str) -> tuple[float, int]:
    wins = total = 0
    for entry in judgments.values():
        order = entry["ranking"]
        if name in order and common.BASELINE in order:
            total += 1
            wins += order.index(name) < order.index(common.BASELINE)
    return (wins / total if total else 0.0), total


def user_rankings() -> dict[str, dict]:
    saved = common.read_json(common.DATA / "user" / "judgments.json", {})
    key = common.read_json(common.DATA / "blind_key.json", {})
    out = {}
    for day, entry in saved.items():
        labels = key.get(day, {})
        ordered = sorted(entry["ranks"], key=lambda label: int(entry["ranks"][label]))
        out[day] = {"ranking": [labels[label] for label in ordered]}
    return out


def build() -> str:
    lines = ["# Phase 2 summary benchmark", ""]
    det = deterministic()
    lines += ["## Shape and speed (deterministic checks)", "",
              "| Model | Days | Pass all shape checks | Avg words | Markdown links | Bare [url] links | Invented URLs | Banned phrases | Sec/day | Tok/s |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for name, d in det.items():
        lines.append(f"| {name} | {d['days']} | {pct(d['shape_pass'])} | {d['words']:.0f} | {d['markdown_links']:.1f} | "
                     f"{d['bare_url_links']:.1f} | {d['invented_urls']} | {d['banned']} | {d['seconds']:.0f} | {d['tok_per_s']:.0f} |")
    pub = [len(common.published_summary(d).split()) for d in common.BENCH_DAYS]
    lines += ["", f"Published summaries average {mean(pub):.0f} words and have no links (the new spec is 220-350 words with inline links).", ""]

    rankers = {"owner": user_rankings()}
    for judge in ("qwen3.8", "gemma4"):
        data = common.read_json(common.DATA / "judge" / f"rank_{judge}.json", {})
        if data:
            rankers[f"judge:{judge}"] = data
    lines += ["## Ranking (1 = best of four)", "", "| Ranker | Days | " + " | ".join(NAMES + [common.BASELINE]) + " |",
              "|---|---|" + "---|" * (len(NAMES) + 1)]
    for who, data in rankers.items():
        if not data:
            continue
        pos = ranks_from(data, "ranking")
        cells = [f"{mean(pos[n]):.2f}" if n in pos else "-" for n in NAMES + [common.BASELINE]]
        lines.append(f"| {who} | {len(data)} | " + " | ".join(cells) + " |")
    lines += ["", "### Win rate against the published summary", "", "| Ranker | " + " | ".join(NAMES) + " |", "|---|" + "---|" * len(NAMES)]
    for who, data in rankers.items():
        if data:
            lines.append(f"| {who} | " + " | ".join(
                f"{pct(win_rate(data, n)[0])} ({win_rate(data, n)[1]} days)" for n in NAMES) + " |")

    lines += ["", "## Claim support (sentence by sentence, by model judges)", "",
              "| Judge | Writer | Sentences | Supported | Unsupported | Status errors |", "|---|---|---|---|---|---|"]
    for judge in ("qwen3.8", "gemma4"):
        data = common.read_json(common.DATA / "judge" / f"support_{judge}.json", {})
        for name in NAMES + [common.BASELINE]:
            rows = [v for k, v in data.items() if k.split("|")[1] == name]
            if not rows:
                continue
            total = sum(r["sentences"] for r in rows)
            lines.append(f"| {judge} | {name} | {total} | {pct(sum(r['supported'] for r in rows) / total)} | "
                         f"{sum(r['unsupported'] for r in rows)} | {sum(r['status_error'] for r in rows)} |")

    for critic in ("qwen3.8", "gemma4"):
        data = common.read_json(common.DATA / "judge" / f"critic_{critic}.json", {})
        if not data:
            continue
        bad = [v for v in data.values() if v["kind"] == "known_bad"]
        good = [v for v in data.values() if v["kind"] == "candidate"]
        lines += ["", f"## Critic ({critic}, thinking on)", "",
                  f"- Rejected {sum(not v['passed'] for v in bad)} of {len(bad)} known-bad summaries (must be all).",
                  f"- Passed {sum(v['passed'] for v in good)} of {len(good)} good candidate summaries (false rejections are expected to be rare).", ""]
        for label, v in data.items():
            lines.append(f"  - {label}: {'PASS' if v['passed'] else 'REJECT'} (avg {v['average']}, {len(v['blocking_defects'])} blocking defects)")

    text = "\n".join(lines) + "\n"
    (common.DATA / "report.md").write_text(text, encoding="utf-8")
    return text


if __name__ == "__main__":
    print(build())
