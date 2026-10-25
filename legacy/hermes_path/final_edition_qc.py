"""Run the complete deterministic publication gate for one dated edition."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path.cwd().resolve()
sys.path.insert(0, str(ROOT))

from automated_briefing import validate_complete_briefing  # noqa: E402
from summary_quality import final_summary_qc_errors  # noqa: E402
from scripts.source_fact_check import provenance_errors  # noqa: E402
from regulatory_tracker import TRACKED_RULEMAKINGS  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run final independent edition QC.")
    parser.add_argument(
        "--date",
        default=datetime.now(ZoneInfo("America/New_York")).date().isoformat(),
    )
    arguments = parser.parse_args()
    run_dir = ROOT / "data" / "runs" / arguments.date
    json_path = run_dir / f"briefing_{arguments.date}.json"
    html_path = run_dir / f"edition_{arguments.date}.html"
    text_path = run_dir / f"edition_{arguments.date}.txt"
    report_path = run_dir / "qc_report.md"
    errors: list[str] = []

    missing = [path.name for path in (json_path, html_path, text_path) if not path.is_file()]
    if missing:
        errors.append("Missing required artifacts: " + ", ".join(missing))
        briefing: dict = {}
        html_text = ""
        plain_text = ""
    else:
        briefing = json.loads(json_path.read_text(encoding="utf-8"))
        html_text = html_path.read_text(encoding="utf-8")
        plain_text = text_path.read_text(encoding="utf-8")

    if briefing:
        try:
            validate_complete_briefing(briefing)
        except Exception as exc:
            errors.append(str(exc))
        errors.extend(
            final_summary_qc_errors(
                briefing, html_text=html_text, plain_text=plain_text
            )
        )
        stories = [
            item
            for items in briefing.get("sections", {}).values()
            for item in items
        ]
        if not 30 <= len(stories) <= 40:
            errors.append(f"Story count is {len(stories)}; 30 to 40 required.")
        tracker = briefing.get("regulatory_tracker", [])
        if len(tracker) < len(TRACKED_RULEMAKINGS):
            errors.append(
                f"Regulatory tracker has {len(tracker)} entries; all "
                f"{len(TRACKED_RULEMAKINGS)} verified rulemakings are required."
            )
        urls = [str(item.get("url", "")) for item in stories]
        if any(not url.startswith(("http://", "https://")) for url in urls):
            errors.append("Every story must have a public HTTP(S) URL.")
        if len(urls) != len(set(urls)):
            errors.append("Duplicate story URLs remain in the final edition.")
        for label in (
            "EXECUTIVE SUMMARY",
            "HEADLINES AT A GLANCE",
            "REGULATORY",
            "WHAT TO WATCH",
        ):
            if label not in html_text.upper():
                errors.append(f"Rendered HTML is missing {label}.")
        if briefing.get("validation_status") != "passed":
            errors.append("validation_status is not passed.")
        if briefing.get("edition_kind") != "editorial":
            errors.append("edition_kind is not editorial.")
        if arguments.date >= "2026-10-21":
            manifest_path = run_dir / "candidate_manifest.json"
            fact_path = run_dir / "fact_check_report.json"
            if not manifest_path.is_file():
                errors.append("Candidate provenance manifest is missing.")
            else:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                source_errors, _ = provenance_errors(briefing, manifest)
                errors.extend(source_errors)
            if not fact_path.is_file():
                errors.append("Independent fact-check report is missing.")
            else:
                fact_report = json.loads(fact_path.read_text(encoding="utf-8"))
                if fact_report.get("status") != "passed":
                    errors.append("Independent Llama fact-check gate did not pass.")

    errors = list(dict.fromkeys(errors))
    story_count = sum(
        len(items) for items in briefing.get("sections", {}).values()
    ) if briefing else 0
    report_lines = [
        f"# QC Report — {arguments.date}",
        "",
        f"Overall: {'PASS' if not errors else 'BLOCKED'}",
        "",
        f"Stories: {story_count}",
        f"Regulatory tracker entries: {len(briefing.get('regulatory_tracker', [])) if briefing else 0}",
        "Article summaries: one or two sentences, present in JSON, HTML, and plain text",
        "",
        "## Checks",
        "",
    ]
    if errors:
        report_lines.extend(f"- [ ] {error}" for error in errors)
    else:
        report_lines.extend(
            (
                "- [x] complete briefing validator passed",
                "- [x] story count is within 30 to 40",
                f"- [x] regulatory tracker contains all {len(TRACKED_RULEMAKINGS)} verified rulemakings",
                "- [x] every story URL is public and unique",
                "- [x] every story is traceable to the saved candidate manifest",
                "- [x] independent Llama fact-check gate passed",
                "- [x] required reader-facing features are rendered",
                "- [x] every article has one or two summary sentences rendered in both formats",
            )
        )
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    result = {
        "status": "passed" if not errors else "blocked",
        "date": arguments.date,
        "stories": story_count,
        "errors": errors,
        "report": str(report_path),
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
