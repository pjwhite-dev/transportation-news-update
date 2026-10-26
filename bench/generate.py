"""Generate the Executive Summary for every benchmark day with every candidate model.

Batched by model (one model load at a time). Resumable: existing outputs are skipped.
    python -m bench.generate [--models qwen3.6 qwen3.8 gemma4] [--days 2026-10-25 ...]
"""

from __future__ import annotations

import argparse
import time

from bench import common, prompts


def generate(model_name: str, day: str, *, force: bool = False) -> dict:
    path = common.DATA / "gen" / model_name / f"{day}.json"
    if path.exists() and not force:
        return common.read_json(path)
    sheet = common.fact_sheet(day)
    messages = [
        {"role": "system", "content": prompts.WRITER_SYSTEM},
        {"role": "user", "content": "FACT SHEET\n\n" + common.fact_sheet_text(sheet) + "\n\nWrite the Executive Summary now."},
    ]
    llm = common.provider(common.MODELS[model_name], think=False, temperature=0.3, num_predict=1500)
    started = time.monotonic()
    result = llm.generate_text(messages, max_output_tokens=1500)
    record = {
        "model": model_name,
        "tag": common.MODELS[model_name],
        "day": day,
        "text": result.text.strip(),
        "seconds": round(time.monotonic() - started, 1),
        "usage": result.usage,
        "retries": result.retries,
    }
    common.write_json(path, record)
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="*", default=list(common.MODELS))
    parser.add_argument("--days", nargs="*", default=common.BENCH_DAYS)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    for model_name in args.models:
        for day in args.days:
            try:
                record = generate(model_name, day, force=args.force)
                words = len(record["text"].split())
                print(f"{model_name} {day}: {words} words, {record['seconds']}s", flush=True)
            except Exception as exc:  # keep going; a failure is recorded, never papered over
                print(f"{model_name} {day}: FAILED {type(exc).__name__}: {str(exc)[:200]}", flush=True)
                common.write_json(common.DATA / "gen" / model_name / f"{day}.failed.json",
                                  {"model": model_name, "day": day, "error": str(exc)[:500]})


if __name__ == "__main__":
    main()
