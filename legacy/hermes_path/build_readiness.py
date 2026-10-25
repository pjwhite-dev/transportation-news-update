"""Emit stable daily build readiness for Hermes monitor-mode recovery."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path.cwd().resolve()
TODAY = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
RUN = ROOT / "data" / "runs" / TODAY
PATHS = {
    "briefing": RUN / f"briefing_{TODAY}.json",
    "html": RUN / f"edition_{TODAY}.html",
    "text": RUN / f"edition_{TODAY}.txt",
    "qc": RUN / "qc_report.md",
}


def main() -> None:
    missing = [name for name, path in PATHS.items() if not path.is_file() or not path.stat().st_size]
    validation = "missing"
    if "briefing" not in missing:
        try:
            validation = str(
                json.loads(PATHS["briefing"].read_text(encoding="utf-8")).get(
                    "validation_status", "missing"
                )
            )
        except Exception:
            validation = "unreadable"
    qc_pass = "qc" not in missing and "Overall: PASS" in PATHS["qc"].read_text(
        encoding="utf-8"
    )
    ready = not missing and validation == "passed" and qc_pass
    print(
        json.dumps(
            {
                "date": TODAY,
                "status": "ready" if ready else "recovery-required",
                "missing": missing,
                "validation_status": validation,
                "qc_pass": qc_pass,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
