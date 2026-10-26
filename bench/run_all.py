"""Run the whole benchmark end to end (resumable; every step skips finished work).

    python -m bench.run_all

Order: generate (all models) -> blind rankings by both model judges -> sentence-support checks
-> critic check -> report. The owner's judging page (bench.serve) can be used as soon as the
generation step has finished.
"""

from __future__ import annotations

import subprocess
import sys
import time

from bench import common, critic, generate, judge, report


def have_model(tag: str) -> bool:
    out = subprocess.run(["ollama", "list"], capture_output=True, text=True).stdout
    return tag in out


def main() -> None:
    models = [m for m in common.MODELS if have_model(common.MODELS[m])]
    missing = [m for m in common.MODELS if m not in models]
    for name in missing:
        print(f"waiting for {common.MODELS[name]} to finish downloading...", flush=True)
    deadline = time.time() + 3600
    while missing and time.time() < deadline:
        time.sleep(30)
        missing = [m for m in missing if not have_model(common.MODELS[m])]
    models = [m for m in common.MODELS if have_model(common.MODELS[m])]
    print("models available:", models, flush=True)

    for name in models:
        for day in common.BENCH_DAYS:
            try:
                record = generate.generate(name, day)
                print(f"gen {name} {day}: {len(record['text'].split())} words, {record['seconds']}s", flush=True)
            except Exception as exc:
                print(f"gen {name} {day}: FAILED {str(exc)[:160]}", flush=True)
    print("GENERATION COMPLETE", flush=True)

    judges = [m for m in ("qwen3.8", "gemma4") if m in models]
    for judge_name in judges:
        judge.run_rank(judge_name, common.BENCH_DAYS)
    print("RANKING COMPLETE", flush=True)
    for judge_name in judges:
        judge.run_support(judge_name, common.BENCH_DAYS)
    print("SUPPORT COMPLETE", flush=True)
    for judge_name in judges:
        critic.run(judge_name)
    print("CRITIC COMPLETE", flush=True)
    print(report.build(), flush=True)
    print("ALL COMPLETE", flush=True)


if __name__ == "__main__":
    sys.exit(main())
