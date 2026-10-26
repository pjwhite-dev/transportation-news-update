"""Replay the production summary chain on archived days and audit the result independently.

    python -m bench.chain_replay [--days ...]      # writes bench/data/chain/<day>.json
    python -m bench.chain_replay --audit           # independent sentence check by qwen3.8 (thinking on)

The chain verifies with gemma4; the audit uses a different judge (qwen3.8) so the check is not
the chain grading itself. Fact sheets here come from the archived stories, exactly as in production.
"""

from __future__ import annotations

import argparse
import re
import time

import summary_chain as chain
from bench import common, judge, prompts

REPLAY_DAYS = ["2026-07-14", "2026-09-14", "2026-09-24", "2026-10-05", "2026-10-25", "2026-10-26"]
LINK = re.compile(r"\[\d+\]\([^)]*\)")


def replay(day: str) -> None:
    path = common.DATA / "chain" / f"{day}.json"
    if path.exists():
        return
    sections = common.load_edition(day)["sections"]
    writer = common.provider(common.MODELS["qwen3.8"], think=False, temperature=0.2, num_predict=1500)
    verifier = chain.verifier_from_env({"OLLAMA_VERIFIER_MODEL": common.MODELS["gemma4"]})
    started = time.monotonic()
    try:
        result = chain.run_chain(writer, verifier, sections)
        record = {"day": day, "ok": True, "text": result.text, "verification": result.verification,
                  "usage": result.usage, "seconds": round(time.monotonic() - started, 1)}
    except chain.SummaryChainError as exc:
        record = {"day": day, "ok": False, "error": str(exc), "seconds": round(time.monotonic() - started, 1)}
    common.write_json(path, record)
    print(day, "OK" if record["ok"] else "REFUSED", record.get("verification", {}).get("rounds"), record["seconds"], "s", flush=True)


def audit(days: list[str]) -> None:
    llm = common.provider(common.MODELS["qwen3.8"], think=True, temperature=0.1, num_predict=12000)
    out_path = common.DATA / "chain" / "audit_qwen3.8.json"
    results = common.read_json(out_path, {})
    for day in days:
        record = common.read_json(common.DATA / "chain" / f"{day}.json")
        if not record or not record.get("ok") or day in results:
            continue
        sheet = chain.build_fact_sheet(common.load_edition(day)["sections"])
        plain = [s for s in common.sentences(LINK.sub("", record["text"]).replace("\n\n", " "))]
        numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(plain, 1))
        body = f"FACT SHEET\n\n{chain.fact_sheet_text(sheet)}\n\nSENTENCES TO CHECK\n{numbered}"
        res = llm.generate_structured(
            [{"role": "system", "content": prompts.JUDGE_SUPPORT_SYSTEM}, {"role": "user", "content": body}],
            schema_name="support", schema=prompts.JUDGE_SUPPORT_SCHEMA, max_output_tokens=12000,
        )
        verdicts = {i["n"]: i for i in res.value["sentences"]}
        counts = {"supported": 0, "unsupported": 0, "status_error": 0}
        for i in range(1, len(plain) + 1):
            counts[verdicts.get(i, {"verdict": "unsupported"})["verdict"]] += 1
        results[day] = {"sentences": len(plain), **counts,
                        "flagged": [{"sentence": plain[i["n"] - 1], "note": i["note"]} for i in res.value["sentences"]
                                    if i["verdict"] != "supported" and 1 <= i["n"] <= len(plain)]}
        common.write_json(out_path, results)
        print("audit", day, counts, flush=True)
    done = [v for v in results.values()]
    total = sum(v["sentences"] for v in done)
    if total:
        print(f"AUDIT supported {sum(v['supported'] for v in done)}/{total} = {100 * sum(v['supported'] for v in done) / total:.0f}%", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", nargs="*", default=REPLAY_DAYS)
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    for day in args.days:
        replay(day)
    if args.audit:
        audit(args.days)


if __name__ == "__main__":
    main()
