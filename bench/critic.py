"""Critic check (plan 7.10 acceptance): the critic must reject every known-bad summary.

Known-bad = the two editions that shipped fallback text (Oct 21 and 22) plus synthetic corruptions
of a good summary. The same critic also scores the good summaries so we can see false rejections.
    python -m bench.critic --critic qwen3.8
"""

from __future__ import annotations

import argparse
import re

from bench import common, prompts
from bench.judge import candidates_for


def corruptions(good: str, sheet: list[dict[str, str]]) -> dict[str, str]:
    first_url = sheet[0]["url"]
    return {
        "template_text": "Key developments include " + "; ".join(s["headline"] for s in sheet[:3]) + ".",
        "headline_list": " ".join(f"{s['headline']}." for s in sheet[:6]),
        "workflow_language": "The supplied records were selected for this briefing. " + good,
        "status_mismatch": re.sub(r"\bproposed\b", "finalized", good, count=1, flags=re.I)
        if re.search(r"\bproposed\b", good, re.I) else good + " The agency finalized the rule this week.",
        "invented_fact": good + f" The administration also committed $4.2 billion to the program [{first_url}].",
    }


def run(critic_name: str) -> None:
    llm = common.provider(common.MODELS[critic_name], think=True, temperature=0.1, num_predict=9000)
    out_path = common.DATA / "judge" / f"critic_{critic_name}.json"
    results = common.read_json(out_path, {})

    def ask(label: str, day: str, text: str, kind: str) -> None:
        if label in results:
            return
        sheet_text = common.fact_sheet_text(common.fact_sheet(day))
        body = f"FACT SHEET\n\n{sheet_text}\n\nSUMMARY\n{text}"
        try:
            result = llm.generate_structured(
                [{"role": "system", "content": prompts.CRITIC_SYSTEM}, {"role": "user", "content": body}],
                schema_name="critic", schema=prompts.CRITIC_SCHEMA, max_output_tokens=9000,
            )
        except Exception as exc:
            print(f"critic {label}: FAILED {str(exc)[:160]}", flush=True)
            return
        v = result.value
        scores = v["scores"]
        average = sum(scores.values()) / len(scores)
        # Plan 7.1: pass = no blocking defects, no dimension below 3, average of at least 4.0.
        passed = not v["blocking_defects"] and min(scores.values()) >= 3 and average >= 4.0 and v["publish"]
        results[label] = {"kind": kind, "day": day, "passed": passed, "average": round(average, 2), "scores": scores,
                          "blocking_defects": v["blocking_defects"]}
        common.write_json(out_path, results)
        print(f"critic {label}: {'PASS' if passed else 'REJECT'} avg={average:.2f} defects={len(v['blocking_defects'])}", flush=True)

    # known-bad shipped editions
    for day in common.KNOWN_BAD_DAYS:
        ask(f"shipped:{day}", day, common.published_summary(day), "known_bad")
    # synthetic corruptions of a good model summary
    base_day = "2026-10-25"
    good = candidates_for(base_day).get("qwen3.6") or common.published_summary(base_day)
    for name, text in corruptions(good, common.fact_sheet(base_day)).items():
        ask(f"synthetic:{name}", base_day, text, "known_bad")
    # good summaries (should pass): the three candidates on three days
    for day in ("2026-10-25", "2026-10-26", "2026-10-05"):
        for name, text in candidates_for(day).items():
            if name != common.BASELINE:
                ask(f"good:{name}:{day}", day, text, "candidate")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--critic", default="qwen3.8")
    run(parser.parse_args().critic)


if __name__ == "__main__":
    main()
