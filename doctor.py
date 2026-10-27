"""Edition doctor: a local-model check that runs after the usual publishing window.

    python doctor.py              # diagnose a missing edition and, if it is a code bug, prepare a fix branch
    python doctor.py --dry-run    # diagnose only; no worktree, no branch, no message

Order of defence: deterministic recovery rules first (they live in the pipeline and the
watchdog), then this doctor for whatever is left. It reads the failing run's log, classifies
the cause with fixed signatures, and only then asks the local model for a diagnosis and, for a
code bug, small search-and-replace edits. Edits go to a doctor/* branch in a separate worktree
and count only if the test suites show no new failures. The doctor never merges, never pushes
to main, never starts an edition and never touches data, trackers, workflows or secrets: the
owner approves any merge.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from alerts import redact, send_telegram

EASTERN = ZoneInfo("America/New_York")
SITE = "https://news.peterjwhite.org/"
EDITION_META = re.compile(r'<meta name="edition-id" content="(\d{4}-\d{2}-\d{2})">')
LOG_TAIL_LINES = 150
CONTEXT_CHARS = 24_000
EDITABLE = re.compile(r"^(?:[A-Za-z0-9_]+\.py|tests/[A-Za-z0-9_]+\.py|tracker/core\.py|bench/[A-Za-z0-9_]+\.py)$")
BLOCKED_NAMES = {"alerts.py", "doctor.py"}

# (signature in the log, cause class, plain-English recovery). First match wins.
SIGNATURES: tuple[tuple[str, str, str], ...] = (
    ("Another edition run is active", "running", "An edition run is still active; wait for it."),
    ("Ollama health check failed", "ollama_down",
     "Ollama was not answering. The 5-minute watchdog restarts it; then run scripts\\run_local_pipeline.ps1."),
    ("repository is not clean", "dirty_tree",
     "Uncommitted files blocked the run. Inspect `git status`, then run scripts\\run_local_pipeline.ps1."),
    ("git push failed", "push_failed", "The local commit exists but did not push. Check GitHub access, then push."),
    ("live deployment was not verified", "deploy_unverified",
     "The push worked but Pages did not show the edition in 5 minutes. Check the GitHub Actions run."),
    ("Complete briefing validation failed", "gate_failure", ""),
    ("Traceback (most recent call last)", "code_error", ""),
)

SYSTEM = """You are the maintenance engineer for a Python news-publishing pipeline that runs locally.
A daily edition failed to publish. You get the tail of the failing run's log, git state, and the
source of the files named in the traceback. Diagnose the root cause. Only if it is a code defect with a
small, certain fix, propose edits as exact search-and-replace pairs: "old" must appear exactly once in the
named file. Never propose weakening, skipping or loosening a validation gate or its thresholds, never touch
data files, workflows, secrets or alerts. If the failure is operational (service down, network, dirty
checkout) or you are not sure, set kind to "operational" or "unknown" and give no edits. Be brief."""

SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["code_fix", "operational", "unknown"]},
        "cause": {"type": "string"},
        "next_step": {"type": "string"},
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"file": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
                "required": ["file", "old", "new"],
            },
        },
    },
    "required": ["kind", "cause", "next_step", "edits"],
}


def live_edition(session: requests.Session | None = None) -> str | None:
    """The edition id currently shown on the live site, or None if it cannot be read."""
    try:
        page = (session or requests).get(SITE, params={"cb": datetime.now().timestamp()}, timeout=20).text
    except requests.RequestException:
        return None
    found = EDITION_META.search(page)
    return found.group(1) if found else None


def classify(log_text: str) -> tuple[str, str]:
    for signature, cause, recovery in SIGNATURES:
        if signature in log_text:
            return cause, recovery
    return "unknown", ""


def latest_log(repo: Path, day: str) -> str:
    logs = sorted((repo / ".runtime" / "logs").glob(f"edition-{day}-*.log"))
    if not logs:
        return ""
    lines = logs[-1].read_text(encoding="utf-8", errors="replace").splitlines()
    return redact("\n".join(lines[-LOG_TAIL_LINES:]))


def traceback_files(log_text: str, repo: Path) -> list[Path]:
    """Repo files named in a traceback, innermost last, each once."""
    seen: list[Path] = []
    for raw in re.findall(r'File "([^"]+\.py)"', log_text):
        path = Path(raw)
        try:
            relative = path.resolve().relative_to(repo.resolve())
        except (ValueError, OSError):
            continue
        if relative.as_posix() not in {p.as_posix() for p in seen} and path.exists() and ".venv" not in relative.parts:
            seen.append(relative)
    return seen


def source_context(repo: Path, files: list[Path]) -> str:
    chunks, budget = [], CONTEXT_CHARS
    for relative in reversed(files):  # innermost frame first
        text = (repo / relative).read_text(encoding="utf-8", errors="replace")[:budget]
        chunks.append(f"=== {relative.as_posix()} ===\n{text}")
        budget -= len(text)
        if budget <= 0:
            break
    return "\n\n".join(chunks)


def allowed_edit(edit: dict[str, Any]) -> bool:
    name = str(edit.get("file", "")).replace("\\", "/")
    return bool(EDITABLE.match(name)) and Path(name).name not in BLOCKED_NAMES and ".." not in name


def apply_edits(root: Path, edits: list[dict[str, Any]]) -> list[str]:
    """Apply search-and-replace edits under ``root``; return the files changed. Raises on any problem."""
    if not edits:
        raise ValueError("No edits to apply.")
    changed: list[str] = []
    staged: dict[Path, str] = {}
    for edit in edits:
        if not allowed_edit(edit):
            raise ValueError(f"Edit to {edit.get('file')!r} is not allowed.")
        path = root / str(edit["file"]).replace("\\", "/")
        text = staged.get(path) or path.read_text(encoding="utf-8")
        if text.count(str(edit["old"])) != 1 or str(edit["old"]) == str(edit["new"]):
            raise ValueError(f"'old' text must appear exactly once in {edit['file']}.")
        staged[path] = text.replace(str(edit["old"]), str(edit["new"]), 1)
        changed.append(str(edit["file"]))
    for path, text in staged.items():
        path.write_text(text, encoding="utf-8", newline="")
    return sorted(set(changed))


def run(args: list[str], cwd: Path, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def failing_tests(python: str, cwd: Path) -> set[str]:
    out = run([python, "-m", "unittest", "discover", "-s", "tests"], cwd).stderr
    return set(re.findall(r"^(?:FAIL|ERROR): (\S+)", out, re.M))


def prepare_branch(repo: Path, day: str, edits: list[dict[str, Any]], python: str) -> tuple[str | None, str]:
    """Apply the edits on a doctor/* branch; keep it only if no test that passed before fails now."""
    branch, tree = f"doctor/{day}", repo / ".wt" / f"doctor-{day}"
    baseline = failing_tests(python, repo)
    if run(["git", "worktree", "add", str(tree), "-b", branch, "main"], repo).returncode != 0:
        return None, "Could not create the doctor worktree."
    try:
        changed = apply_edits(tree, edits)
    except (ValueError, OSError) as exc:
        return None, f"The proposed edit was rejected: {exc}"
    after = failing_tests(python, tree)
    if after - baseline:
        run(["git", "checkout", "--", "."], tree)
        return None, f"The proposed edit broke {len(after - baseline)} test(s); discarded."
    run(["git", "add", "-A"], tree)
    run(["git", "commit", "-m", f"Doctor: proposed fix for the {day} edition failure ({', '.join(changed)})"], tree)
    return branch, f"Fix prepared on branch {branch} ({', '.join(changed)}); tests show no new failures."


def diagnose(log_text: str, repo: Path, files: list[Path], provider: Any) -> dict[str, Any]:
    status = run(["git", "status", "--short"], repo).stdout.strip()[:600]
    body = f"LOG TAIL\n{log_text}\n\nGIT STATUS\n{status or '(clean)'}\n\nSOURCE\n{source_context(repo, files)}"
    result = provider.generate_structured(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": body}],
        schema_name="diagnosis", schema=SCHEMA, max_output_tokens=4000,
    )
    return result.value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    repo = args.repo.resolve()
    now = datetime.now(EASTERN)
    day = now.date().isoformat()
    if now.weekday() >= 5 or live_edition() == day:
        return 0
    try:
        with open(repo / ".runtime" / "edition.lock", "a+"):
            pass
    except OSError:
        print("An edition run holds the lock; not interfering.")
        return 0

    log_text = latest_log(repo, day)
    cause, recovery = classify(log_text)
    message = f"Doctor: no edition for {day}. Cause: {cause}."
    if cause == "running":
        return 0
    if recovery:
        message += f" {recovery}"
    elif log_text and cause in ("gate_failure", "code_error", "unknown"):
        from ai_providers import provider_from_env

        try:
            verdict = diagnose(log_text, repo, traceback_files(log_text, repo), provider_from_env())
        except Exception as exc:  # the model itself may be down; the log classification still stands
            verdict = {"kind": "unknown", "cause": f"model unavailable ({type(exc).__name__})", "next_step": "Read the run log.", "edits": []}
        message += f" {verdict['cause']} Next: {verdict['next_step']}"
        if verdict["kind"] == "code_fix" and verdict["edits"] and not args.dry_run:
            python = str(repo / ".venv" / "Scripts" / "python.exe") if (repo / ".venv").exists() else sys.executable
            branch, note = prepare_branch(repo, day, verdict["edits"], python)
            message += f" {note}"
            if branch:
                message += " Review and merge it yourself."
    message = redact(message)[:900]
    print(message)
    if not args.dry_run:
        send_telegram(message, critical=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
