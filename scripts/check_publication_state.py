"""Print whether a scheduled local publication is still needed today."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def publication_needed(
    briefing_path: Path,
    mode: str,
    today: str,
) -> bool:
    """Use the saved complete briefing, not n8n's transient cursor, as the guard."""
    try:
        briefing = json.loads(briefing_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    if briefing.get("validation_status") != "passed":
        return True
    try:
        briefing_day = (
            datetime.fromisoformat(str(briefing["generated_at"]))
            .astimezone(EASTERN)
            .date()
            .isoformat()
        )
    except (KeyError, ValueError, TypeError):
        return True
    if briefing_day != today:
        return True
    if mode == "any":
        return False
    extracted = int(briefing.get("supplemental_count", 0) or 0)
    represented = int(briefing.get("supplemental_accounted_count", 0) or 0)
    return not (extracted > 0 and represented == extracted)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("any", "supplemental"), required=True)
    parser.add_argument(
        "--briefing",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "latest_briefing.json",
    )
    args = parser.parse_args()
    today = datetime.now(EASTERN).date().isoformat()
    print("READY" if publication_needed(args.briefing, args.mode, today) else "SKIP")


if __name__ == "__main__":
    main()
