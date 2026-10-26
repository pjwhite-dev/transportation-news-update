"""Model judges: blind ranking and sentence-level support checks.

    python -m bench.judge rank    --judge qwen3.8 gemma4
    python -m bench.judge support --judge qwen3.8 gemma4

Judges are local models (thinking on). Ranking shows each day's four summaries (three
candidates plus the published one) under shuffled labels; the key is stored separately.
"""

from __future__ import annotations

import argparse
import json
import random

from bench import common, prompts


def candidates_for(day: str) -> dict[str, str]:
    """name -> summary text, for every model that produced one plus the published baseline."""
    out = {common.BASELINE: common.published_summary(day)}
    for name in common.MODELS:
        record = common.read_json(common.DATA / "gen" / name / f"{day}.json")
        if record and record.get("text"):
            out[name] = record["text"]
    return out


def blind_key(day: str, names: list[str]) -> dict[str, str]:
    """Stable per-day shuffle label -> candidate name (seeded so reruns agree)."""
    key_path = common.DATA / "blind_key.json"
    keys = common.read_json(key_path, {})
    if day not in keys or sorted(keys[day].values()) != sorted(names):
        rng = random.Random(f"bench-{day}")
        shuffled = names[:]
        rng.shuffle(shuffled)
        keys[day] = {label: name for label, name in zip("ABCD", shuffled)}
        common.write_json(key_path, keys)
    return keys[day]


def run_rank(judge_name: str, days: list[str]) -> None:
    llm = common.provider(common.MODELS[judge_name], think=True, temperature=0.1, num_predict=9000)
    out_path = common.DATA / "judge" / f"rank_{judge_name}.json"
    results = common.read_json(out_path, {})
    for day in days:
        if day in results:
            continue
        cands = candidates_for(day)
        names = sorted(cands)
        if len(names) != 4:
            print(f"{judge_name} rank {day}: skipped ({len(names)} candidates)", flush=True)
            continue
        key = blind_key(day, names)
        body = "FACT SHEET\n\n" + common.fact_sheet_text(common.fact_sheet(day)) + "\n\n"
        for label in "ABCD":
            body += f"SUMMARY {label}\n{cands[key[label]]}\n\n"
        body += "Rank the four summaries from best to worst."
        try:
            result = llm.generate_structured(
                [{"role": "system", "content": prompts.JUDGE_RANK_SYSTEM}, {"role": "user", "content": body}],
                schema_name="rank", schema=prompts.JUDGE_RANK_SCHEMA, max_output_tokens=9000,
            )
        except Exception as exc:
            print(f"{judge_name} rank {day}: FAILED {str(exc)[:160]}", flush=True)
            continue
        order = [key[label] for label in result.value["ranking"]]
        if len(set(order)) != 4:
            print(f"{judge_name} rank {day}: invalid ranking {result.value['ranking']}", flush=True)
            continue
        results[day] = {"ranking": order, "labels": result.value["ranking"], "reason": result.value["reason"]}
        common.write_json(out_path, results)
        print(f"{judge_name} rank {day}: {' > '.join(order)}", flush=True)


def run_support(judge_name: str, days: list[str]) -> None:
    llm = common.provider(common.MODELS[judge_name], think=True, temperature=0.1, num_predict=12000)
    out_path = common.DATA / "judge" / f"support_{judge_name}.json"
    results = common.read_json(out_path, {})
    for day in days:
        sheet_text = common.fact_sheet_text(common.fact_sheet(day))
        for name, text in candidates_for(day).items():
            cache_key = f"{day}|{name}"
            if cache_key in results:
                continue
            plain = [s for s in common.sentences(text)]
            numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(plain, start=1))
            body = f"FACT SHEET\n\n{sheet_text}\n\nSENTENCES TO CHECK\n{numbered}"
            try:
                result = llm.generate_structured(
                    [{"role": "system", "content": prompts.JUDGE_SUPPORT_SYSTEM}, {"role": "user", "content": body}],
                    schema_name="support", schema=prompts.JUDGE_SUPPORT_SCHEMA, max_output_tokens=12000,
                )
            except Exception as exc:
                print(f"{judge_name} support {cache_key}: FAILED {str(exc)[:160]}", flush=True)
                continue
            verdicts = {item["n"]: item["verdict"] for item in result.value["sentences"]}
            counts = {"supported": 0, "unsupported": 0, "status_error": 0}
            for i in range(1, len(plain) + 1):
                counts[verdicts.get(i, "unsupported")] += 1  # an unanswered sentence counts against the summary
            results[cache_key] = {"sentences": len(plain), **counts, "detail": result.value["sentences"]}
            common.write_json(out_path, results)
            print(f"{judge_name} support {cache_key}: {counts}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task", choices=["rank", "support"])
    parser.add_argument("--judge", nargs="+", default=["qwen3.8", "gemma4"])
    parser.add_argument("--days", nargs="*", default=common.BENCH_DAYS)
    args = parser.parse_args()
    for judge_name in args.judge:
        (run_rank if args.task == "rank" else run_support)(judge_name, args.days)


if __name__ == "__main__":
    main()
