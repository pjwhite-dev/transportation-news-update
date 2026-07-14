from __future__ import annotations

import os
from pathlib import Path

from news_engine import DEFAULT_OPENAI_MODEL, generate_daily_briefing, write_briefing


def main() -> None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip()

    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not configured.")

    root = Path(__file__).resolve().parent
    briefing = generate_daily_briefing(api_key=api_key, model=model)
    latest, archive = write_briefing(briefing, root)

    usage = briefing.get("usage", {})
    cost = briefing.get("estimated_cost")

    print(f"Wrote {latest}")
    print(f"Archived {archive}")
    print(
        f"Coverage: {briefing['window_start']} through {briefing['window_end']} | "
        f"Candidates: {briefing.get('candidate_count', 0)} | "
        f"Tokens: {usage.get('input_tokens', 0)} in / "
        f"{usage.get('output_tokens', 0)} out"
    )

    if cost is not None:
        print(f"Estimated OpenAI cost: ${cost:.4f}")


if __name__ == "__main__":
    main()
