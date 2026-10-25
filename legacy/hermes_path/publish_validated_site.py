"""Publish one validated Hermes edition to the existing GitHub Pages source.

The commit is built directly from origin/main with a temporary Git index. This
keeps every unrelated working-tree change out of the publication commit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path.cwd().resolve()
if not (ROOT / ".git").exists() or not (ROOT / "AGENTS.md").is_file():
    raise RuntimeError(
        "Publisher must run from the Advanced Transportation Update repository root."
    )
sys.path.insert(0, str(ROOT))
from summary_quality import final_summary_qc_errors  # noqa: E402
RUNTIME = ROOT / ".runtime" / "atu-publication"
EASTERN = ZoneInfo("America/New_York")
SYSTEM_GIT = Path(r"C:\Program Files\Git\cmd\git.exe")
GIT_EXE = str(SYSTEM_GIT if SYSTEM_GIT.is_file() else (shutil.which("git") or "git"))


def git(*arguments: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        [GIT_EXE, *arguments],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(
            f"git {' '.join(arguments)} failed with code {completed.returncode}"
            + (f": {detail}" if detail else "")
        )
    return completed.stdout.strip()


def remote_file(path: str) -> bytes | None:
    completed = subprocess.run(
        [GIT_EXE, "show", f"origin/main:{path}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if completed.returncode == 0:
        return completed.stdout
    if completed.returncode == 128:
        return None
    raise RuntimeError(completed.stderr.decode("utf-8", errors="replace").strip())


def validate(edition_date: str) -> tuple[Path, bytes]:
    run_dir = ROOT / "data" / "runs" / edition_date
    briefing_path = run_dir / f"briefing_{edition_date}.json"
    html_path = run_dir / f"edition_{edition_date}.html"
    text_path = run_dir / f"edition_{edition_date}.txt"
    qc_path = run_dir / "qc_report.md"
    for required in (briefing_path, html_path, text_path, qc_path):
        if not required.is_file() or required.stat().st_size == 0:
            raise RuntimeError(f"Required publication artifact is missing: {required}")

    briefing = json.loads(briefing_path.read_text(encoding="utf-8"))
    actual_date = ""
    for key in ("window_end", "generated_at"):
        value = str(briefing.get(key, ""))
        try:
            actual_date = datetime.fromisoformat(value).date().isoformat()
            break
        except ValueError:
            continue
    if actual_date != edition_date:
        raise RuntimeError(
            f"Edition date mismatch: requested {edition_date}, briefing is {actual_date or 'unknown'}"
        )
    if briefing.get("validation_status") != "passed":
        raise RuntimeError("Briefing validation_status is not 'passed'.")
    if briefing.get("edition_kind") != "editorial":
        raise RuntimeError("Briefing is not a complete editorial edition.")

    if "Overall: PASS" not in qc_path.read_text(encoding="utf-8"):
        raise RuntimeError("QC report does not contain 'Overall: PASS'.")

    summary_errors = final_summary_qc_errors(
        briefing,
        html_text=html_path.read_text(encoding="utf-8"),
        plain_text=text_path.read_text(encoding="utf-8"),
    )
    if summary_errors:
        raise RuntimeError("Final summary QC failed:\n- " + "\n- ".join(summary_errors))

    payload = json.dumps(briefing, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    return briefing_path, payload


def publish(edition_date: str, *, dry_run: bool) -> dict[str, object]:
    briefing_path, payload = validate(edition_date)
    archive_path = f"data/archive/{edition_date}.json"
    latest_path = "data/latest_briefing.json"
    receipt_path = RUNTIME / f"{edition_date}.json"
    revision_marker = RUNTIME / f"{edition_date}.revision-authorized"

    git("fetch", "origin", "main")
    existing_archive = remote_file(archive_path)
    existing_latest = remote_file(latest_path)
    authorized_revision = existing_archive is not None and existing_archive != payload
    if authorized_revision and not revision_marker.is_file():
        raise RuntimeError(
            f"Remote archive already contains different content for {edition_date}; refusing overwrite."
        )
    if existing_archive == payload and existing_latest == payload:
        if receipt_path.is_file():
            return {"status": "already-published", "date": edition_date}
        # A dry-run push changes nothing but proves the unattended Hermes
        # environment can use the configured Git credential helper.
        git("push", "--dry-run", "origin", "origin/main:refs/heads/main")
        RUNTIME.mkdir(parents=True, exist_ok=True)
        receipt = {
            "status": "already-published",
            "date": edition_date,
            "auth_verified": True,
            "verified_at": datetime.now(EASTERN).isoformat(),
            "paths": [latest_path, archive_path],
        }
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        return receipt

    digest = hashlib.sha256(payload).hexdigest()
    if dry_run:
        return {
            "status": "dry-run-ready",
            "date": edition_date,
            "source": str(briefing_path),
            "sha256": digest,
            "revision": authorized_revision,
        }

    RUNTIME.mkdir(parents=True, exist_ok=True)
    index_path = RUNTIME / f"index-{edition_date}"
    payload_path = RUNTIME / f"payload-{edition_date}.json"
    index_path.unlink(missing_ok=True)
    payload_path.write_bytes(payload)
    environment = os.environ.copy()
    environment["GIT_INDEX_FILE"] = str(index_path)
    try:
        git("read-tree", "origin/main", env=environment)
        blob = git("hash-object", "-w", str(payload_path))
        for path in (latest_path, archive_path):
            git(
                "update-index",
                "--add",
                "--cacheinfo",
                f"100644,{blob},{path}",
                env=environment,
            )
        tree = git("write-tree", env=environment)
        parent = git("rev-parse", "origin/main")
        commit = git(
            "commit-tree",
            tree,
            "-p",
            parent,
            "-m",
            f"{'Revise' if authorized_revision else 'Publish'} Hermes news edition {edition_date}",
        )
        git("push", "origin", f"{commit}:refs/heads/main")
    finally:
        index_path.unlink(missing_ok=True)
        payload_path.unlink(missing_ok=True)

    receipt = {
        "status": "published",
        "date": edition_date,
        "commit": commit,
        "sha256": digest,
        "published_at": datetime.now(EASTERN).isoformat(),
        "paths": [latest_path, archive_path],
        "revision": authorized_revision,
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    if authorized_revision:
        revision_marker.unlink(missing_ok=True)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a validated Hermes ATU edition.")
    parser.add_argument("--date", default=datetime.now(EASTERN).date().isoformat())
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    try:
        result = publish(arguments.date, dry_run=arguments.dry_run)
    except Exception as exc:
        message = str(exc)
        now = datetime.now(EASTERN)
        if (
            message.startswith("Required publication artifact is missing:")
            and arguments.date == now.date().isoformat()
            and now.hour < 13
        ):
            print(
                json.dumps(
                    {
                        "status": "waiting-for-build",
                        "date": arguments.date,
                        "next_action": "A later scheduled publication check will retry automatically.",
                    }
                )
            )
            return 0
        print(json.dumps({"status": "blocked", "date": arguments.date, "error": message}))
        return 1
    if result["status"] != "already-published":
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
