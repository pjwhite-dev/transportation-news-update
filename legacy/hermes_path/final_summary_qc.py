"""Final deterministic one-or-two-sentence summary gate for a dated edition."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path.cwd().resolve()
sys.path.insert(0, str(ROOT))

from summary_quality import final_summary_qc_errors  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate final article summaries.")
    parser.add_argument(
        "--date",
        default=datetime.now(ZoneInfo("America/New_York")).date().isoformat(),
    )
    arguments = parser.parse_args()
    run_dir = ROOT / "data" / "runs" / arguments.date
    briefing_path = run_dir / f"briefing_{arguments.date}.json"
    html_path = run_dir / f"edition_{arguments.date}.html"
    text_path = run_dir / f"edition_{arguments.date}.txt"
    missing = [str(path) for path in (briefing_path, html_path, text_path) if not path.is_file()]
    if missing:
        print(json.dumps({"status": "blocked", "missing": missing}))
        return 1
    briefing = json.loads(briefing_path.read_text(encoding="utf-8"))
    errors = final_summary_qc_errors(
        briefing,
        html_text=html_path.read_text(encoding="utf-8"),
        plain_text=text_path.read_text(encoding="utf-8"),
    )
    report = {
        "status": "passed" if not errors else "blocked",
        "date": arguments.date,
        "check": "every article has one or two rendered summary sentences",
        "errors": errors,
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
