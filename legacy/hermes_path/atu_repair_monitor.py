"""Stable monitor output that wakes a repair agent only when failure mode changes."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path.cwd().resolve()
EASTERN = ZoneInfo("America/New_York")


def main() -> None:
    day = datetime.now(EASTERN).date().isoformat()
    run = ROOT / "data" / "runs" / day
    qc = run / "qc_report.md"
    fact = run / "fact_check_report.json"
    if qc.is_file() and "Overall: PASS" in qc.read_text(encoding="utf-8") and fact.is_file():
        payload = json.loads(fact.read_text(encoding="utf-8"))
        if payload.get("status") == "passed":
            print(json.dumps({"date": day, "status": "healthy"}, sort_keys=True))
            return
    failure = ROOT / ".runtime" / "atu-automation" / "last_failure.json"
    error = "daily edition is not ready"
    if failure.is_file():
        try:
            error = str(json.loads(failure.read_text(encoding="utf-8")).get("error") or error)
        except (OSError, ValueError):
            error = "last failure record is unreadable"
    print(json.dumps({"date": day, "status": "repair-required", "error": error}, sort_keys=True))


if __name__ == "__main__":
    main()
