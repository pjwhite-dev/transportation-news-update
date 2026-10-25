"""Retry transient daily-build failures from saved source data."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = (
    Path.cwd().resolve()
    if (Path.cwd() / "news_engine.py").is_file()
    else Path(__file__).resolve().parents[1]
)
EASTERN = ZoneInfo("America/New_York")


def main() -> int:
    day = datetime.now(EASTERN).date().isoformat()
    run = ROOT / "data" / "runs" / day
    qc = run / "qc_report.md"
    fact = run / "fact_check_report.json"
    if qc.is_file() and "Overall: PASS" in qc.read_text(encoding="utf-8") and fact.is_file():
        payload = json.loads(fact.read_text(encoding="utf-8"))
        if payload.get("status") == "passed":
            return 0
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    result = subprocess.run(
        [str(python), str(ROOT / "scripts" / "hermes_daily_build.py")],
        cwd=ROOT,
        check=False,
        timeout=3 * 3600,
    )
    if result.returncode:
        failure = ROOT / ".runtime" / "atu-automation" / "last_failure.json"
        detail = failure.read_text(encoding="utf-8") if failure.is_file() else "{}"
        print("ATU_AUTOMATION_REPAIR_REQUIRED " + detail)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
