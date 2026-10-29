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
EDITABLE = re.compile(
    r"^(?:[A-Za-z0-9_]+\.py|tests/[A-Za-z0-9_]+\.py|tracker/core\.py|bench/[A-Za-z0-9_]+\.py"
    r"|scripts/[A-Za-z0-9_]+\.(?:ps1|py)|config/[A-Za-z0-9_]+\.yaml|tracker/rulemakings\.yaml)$"
)
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
    ("TerminatingError(", "script_error", ""),  # a PowerShell step stopped the run (2026-10-28: git checkout)
)

# Causes the doctor cures by running the edition again (the pipeline's own preflight clears stale files).
RERUN_CAUSES = {"ollama_down", "dirty_tree"}

SYSTEM = """You are the maintenance engineer for a Python news-publishing pipeline that runs locally.
A daily edition failed to publish. You get the tail of the failing run's log, git state, and the
source of the files named in the traceback. Diagnose the root cause. Only if it is a code defect with a
small, certain fix, propose edits as exact search-and-replace pairs: "old" must appear exactly once in the
named file. Never propose weakening, skipping or loosening a validation gate or its thresholds, never touch
data files, workflows, secrets or alerts. A code_fix must also include "regression_test": the complete
source of a new unittest module that FAILS on the current code and PASSES once your edits are applied (import
the project modules directly, use only in-memory data, no network, no model calls). If the failure is
operational (service down, network, dirty checkout) or you are not sure, set kind to "operational" or
"unknown", give no edits and an empty regression_test.
A validation-gate failure caused by ONE bad item (one dead link, one blank or templated summary, one
duplicate story) is NOT operational: the edition should not be lost to a single story. The gates stay
exactly as they are; the fix is a deterministic repair that runs BEFORE the gate, in validation_gates.py
(see repair_story_failures and prune_unusable_stories) or automated_briefing.py (repair_briefing): drop or
blank the offending item, leaving enough stories. If a repair for that evidence already exists and still
failed, fix why it missed (for example an unmatched evidence pattern). Only when the failure is genuinely
edition-wide, such as too few stories, or external, such as the whole network down, use "operational".
Be brief."""

DIAGNOSIS_ATTEMPTS = 4

GATE_TASK = (
    "\n\nTASK: a validation gate rejected the finished edition. Unless the evidence is edition-wide "
    "(too few stories, whole-network outage), kind must be code_fix: add a deterministic repair that "
    "removes or blanks the offending item BEFORE the gate runs, leave the gate untouched, and include a "
    "regression_test that exercises the repair."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["code_fix", "operational", "unknown"]},
        "cause": {"type": "string"},
        "next_step": {"type": "string"},
        "regression_test": {"type": "string"},
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"file": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
                "required": ["file", "old", "new"],
            },
        },
    },
    "required": ["kind", "cause", "next_step", "edits", "regression_test"],
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
    """The tail of the newest log that says something; an attempt that wrote nothing hides the real cause."""
    logs = sorted((repo / ".runtime" / "logs").glob(f"edition-{day}-*.log"))
    for log in reversed(logs):
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        if any(line.strip() for line in lines):
            return redact("\n".join(lines[-LOG_TAIL_LINES:]))
    return ""


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


def context_files(cause: str, log_text: str, repo: Path) -> list[Path]:
    """Traceback files, plus the repair rules for a gate failure (placed last so they are shown first)."""
    files = traceback_files(log_text, repo)
    if cause == "script_error":
        for raw in re.findall(r"-File\s+(\S+\.ps1)", log_text):
            try:
                relative = Path(raw).resolve().relative_to(repo.resolve())
            except (ValueError, OSError):
                continue
            if (repo / relative).exists() and relative not in files:
                files.append(relative)
    gates = Path("validation_gates.py")
    if cause == "gate_failure" and (repo / gates).exists() and gates not in files:
        files.append(gates)
    return files


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


def run_test_module(python: str, cwd: Path, module: str) -> bool:
    return run([python, "-m", "unittest", module], cwd).returncode == 0


def prepare_branch(
    repo: Path, day: str, edits: list[dict[str, Any]], regression_test: str, python: str,
) -> tuple[str | None, str]:
    """Keep a doctor/* branch only if a new test fails before the edits, passes after, and nothing else breaks."""
    branch, tree = f"doctor/{day}", repo / ".wt" / f"doctor-{day}"
    module, relative = f"tests.test_incident_{day.replace('-', '_')}", f"tests/test_incident_{day.replace('-', '_')}.py"
    if not regression_test.strip() or "def test" not in regression_test:
        return None, "No regression test was proposed, so the fix was not kept."
    baseline = failing_tests(python, repo)
    if run(["git", "worktree", "add", str(tree), "-b", branch, "main"], repo).returncode != 0:
        return None, "Could not create the doctor worktree."
    (tree / relative).write_text(regression_test, encoding="utf-8", newline="")
    if run_test_module(python, tree, module):
        return None, "The proposed regression test passes on the unfixed code, so it does not reproduce the failure; discarded."
    try:
        changed = apply_edits(tree, edits)
    except (ValueError, OSError) as exc:
        return None, f"The proposed edit was rejected: {exc}"
    if not run_test_module(python, tree, module):
        return None, "The proposed edit does not make its own regression test pass; discarded."
    after = failing_tests(python, tree)
    if after - baseline:
        return None, f"The proposed edit broke {len(after - baseline)} existing test(s); discarded."
    run(["git", "add", "-A"], tree)
    run(["git", "commit", "-m", f"Doctor: proposed fix for the {day} edition failure ({', '.join(changed)}) with regression test"], tree)
    return branch, f"Fix prepared on branch {branch} ({', '.join(changed)}) with a regression test that fails before and passes after."


def incident_key(cause: str, log_text: str) -> str:
    """Stable label for 'the same kind of failure': cause class plus the last error line, digits and URLs removed."""
    errors = re.findall(r"^\s*([A-Za-z_.]*(?:Error|Exception)[^\n]*)$", log_text, re.M) or re.findall(r"^- (.+)$", log_text, re.M)
    line = errors[-1] if errors else ""
    line = re.sub(r"https?://\S+|\d+", "#", line)
    gates = sorted(set(re.findall(r"^- ([a-z_]+):", log_text, re.M)))
    return f"{cause}: {line.strip()[:120]} [{','.join(gates)}]"


AUTO_MAX_ADDED, AUTO_MAX_REMOVED = 150, 30
_WEAKENING = re.compile(r"\braise\b|\bassert\b|\bMIN\w*|\bMAX\w*|threshold|\bGATE\w*", re.I)
_SUPPRESSING = re.compile(r"\bskip\w*\b|noqa|except\s+Exception\s*:\s*(?:pass|continue)|\bxfail\b", re.I)


def auto_merge_ok(repo: Path, branch: str) -> tuple[bool, str]:
    """Deterministic guard for self-approval: a small, additive change that cannot be weakening a gate."""
    stat = run(["git", "diff", "--numstat", f"main...{branch}"], repo)
    if stat.returncode != 0 or not stat.stdout.strip():
        return False, "could not read the diff"
    added = removed = 0
    new_tests = 0
    for line in stat.stdout.splitlines():
        a, r, name = line.split("\t", 2)
        if a == "-":
            return False, "binary file in diff"
        added, removed = added + int(a), removed + int(r)
        if name.startswith("tests/test_incident_"):
            new_tests += 1
        elif not EDITABLE.match(name) or name.startswith("tests/") or Path(name).name in BLOCKED_NAMES:
            return False, f"touches {name}"
    if new_tests != 1:
        return False, "no single new regression test"
    if added > AUTO_MAX_ADDED or removed > AUTO_MAX_REMOVED:
        return False, f"change too large (+{added}/-{removed})"
    body = run(["git", "diff", "-U0", f"main...{branch}", "--", ":(exclude)tests"], repo).stdout
    for line in body.splitlines():
        if line.startswith("-") and not line.startswith("---") and _WEAKENING.search(line):
            return False, "removes a check or threshold"
        if line.startswith("+") and not line.startswith("+++") and _SUPPRESSING.search(line):
            return False, "adds a skip or error suppression"
    return True, ""


def apply_branch(repo: Path, branch: str) -> str:
    """Run the one-step approval (fast-forward main, push, rerun); report how it went."""
    script = repo / "scripts" / "approve_doctor.ps1"
    try:
        result = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Branch", branch, "-Yes"],
                     repo, timeout=3600)
    except subprocess.TimeoutExpired:
        return "The rerun timed out; check the log."
    return "Rerun finished." if result.returncode == 0 else "The merge or rerun reported an error; check the log."


def claim_rerun(repo: Path, day: str) -> bool:
    """One automatic rerun per day, so a failure that persists cannot loop."""
    marker = repo / ".runtime" / f"doctor-rerun-{day}.marker"
    if marker.exists():
        return False
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(datetime.now(EASTERN).isoformat(), encoding="utf-8")
    return True


def rerun_edition(repo: Path) -> str:
    script = repo / "scripts" / "run_local_pipeline.ps1"
    try:
        result = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)], repo, timeout=3600)
    except subprocess.TimeoutExpired:
        return "The rerun timed out; check the log."
    return "The rerun finished." if result.returncode == 0 else "The rerun failed again; the next log will be diagnosed."


CI_API = "https://api.github.com/repos/pjwhite-dev/transportation-news-update"
CI_UNREACHABLE = re.compile(r"links: \S+ returned HTTP (?:None|5\d\d): (https?://\S+)")


def ci_unreachable_hosts(sha: str) -> list[str]:
    """Hosts the GitHub re-check could not reach for this commit (public API; the workflow echoes gate failures
    as annotations). 2026-10-28: finance.biggo.com refuses GitHub's network, so the site never deployed."""
    hosts: list[str] = []
    try:
        runs = requests.get(f"{CI_API}/actions/runs", params={"head_sha": sha, "per_page": 5}, timeout=20).json()
        for item in runs.get("workflow_runs", []):
            if not str(item.get("name", "")).startswith("Publish Transportation News Website") or item.get("conclusion") != "failure":
                continue
            jobs = requests.get(f"{CI_API}/actions/runs/{item['id']}/jobs", timeout=20).json().get("jobs", [])
            for job in jobs:
                notes = requests.get(f"{CI_API}/check-runs/{job['id']}/annotations", timeout=20).json()
                for note in notes if isinstance(notes, list) else []:
                    for url in CI_UNREACHABLE.findall(str(note.get("message", ""))):
                        host = (re.match(r"https?://([^/]+)", url).group(1) or "").lower().removeprefix("www.")
                        if host and host not in hosts:
                            hosts.append(host)
    except (requests.RequestException, ValueError, KeyError):
        return []
    return hosts


def deny_hosts_in_config(repo: Path, hosts: list[str]) -> list[str]:
    """Add hosts to deny_hosts in config/editorial.yaml (retail and quote sites are denied by the editorial rules)."""
    path = repo / "config" / "editorial.yaml"
    text = path.read_text(encoding="utf-8")
    added = [h for h in hosts if re.search(rf"^\s*-\s*{re.escape(h)}\s*$", text, re.M) is None]
    if added and "deny_hosts:\n" in text:
        text = text.replace("deny_hosts:\n", "deny_hosts:\n" + "".join(f"  - {h}\n" for h in added), 1)
        path.write_text(text, encoding="utf-8")
        return added
    return []


def deny_unreachable_hosts_and_rerun(repo: Path, day: str) -> str:
    """Recovery rule for a CI link failure: deny the host, commit and push the config, rebuild the edition."""
    sha = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    hosts = ci_unreachable_hosts(sha)
    if not hosts:
        return ""
    if not claim_rerun(repo, day):
        return f"CI could not reach {', '.join(hosts)}, but today's automatic rerun was already used."
    added = deny_hosts_in_config(repo, hosts)
    if added:
        run(["git", "add", "--", "config/editorial.yaml"], repo)
        run(["git", "commit", "-m", f"Deny hosts that refuse GitHub's network: {', '.join(added)}\n\nCo-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"], repo)
        if run(["git", "push", "origin", "HEAD:main"], repo).returncode != 0:
            return f"Denied {', '.join(added)} locally but the push failed."
    return f"CI could not reach {', '.join(hosts)}; denied {', '.join(added) or 'nothing new'} and rebuilt the edition. {rerun_edition(repo)}"


def incident_count(repo: Path, key: str) -> int:
    ledger = repo / ".runtime" / "incidents.jsonl"
    if not ledger.exists():
        return 0
    count = 0
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            count += json.loads(line).get("key") == key
        except ValueError:
            continue
    return count


def record_incident(repo: Path, day: str, key: str, branch: str | None) -> int:
    """Append to the incident ledger (kept in .runtime, not in git); return how many times this key was seen before."""
    ledger = repo / ".runtime" / "incidents.jsonl"
    ledger.parent.mkdir(exist_ok=True)
    seen = 0
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                seen += json.loads(line).get("key") == key
            except ValueError:
                continue
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"day": day, "key": key, "branch": branch}) + "\n")
    return seen


def diagnose(log_text: str, repo: Path, files: list[Path], provider: Any) -> dict[str, Any]:
    status = run(["git", "status", "--short"], repo).stdout.strip()[:600]
    body = f"LOG TAIL\n{log_text}\n\nGIT STATUS\n{status or '(clean)'}\n\nSOURCE\n{source_context(repo, files)}"
    if "Complete briefing validation failed" in log_text:
        body += GATE_TASK
    verdict: dict[str, Any] = {}
    for _ in range(DIAGNOSIS_ATTEMPTS):  # a code_fix with no usable edits or test is a miss, not a verdict
        verdict = provider.generate_structured(
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": body}],
            schema_name="diagnosis", schema=SCHEMA, max_output_tokens=4000,
        ).value
        if verdict.get("kind") != "code_fix" or (verdict.get("edits") and verdict.get("regression_test")):
            break
    return verdict


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
    branch_made: str | None = None
    if cause == "running":
        return 0
    if recovery:
        message += f" {recovery}"
        if cause in RERUN_CAUSES and not args.dry_run and claim_rerun(repo, day):
            message += f" Rerunning the edition now. {rerun_edition(repo)}"
        if cause == "deploy_unverified" and not args.dry_run:
            message += " " + deny_unreachable_hosts_and_rerun(repo, day)
    elif log_text and cause in ("gate_failure", "code_error", "script_error", "unknown"):
        from ai_providers import provider_from_env

        try:
            verdict = diagnose(log_text, repo, context_files(cause, log_text, repo), provider_from_env())
        except Exception as exc:  # the model itself may be down; the log classification still stands
            verdict = {"kind": "unknown", "cause": f"model unavailable ({type(exc).__name__})", "next_step": "Read the run log.", "edits": []}
        message += f" {verdict['cause']} Next: {verdict['next_step']}"
        if verdict["kind"] == "code_fix" and verdict["edits"] and not args.dry_run:
            python = str(repo / ".venv" / "Scripts" / "python.exe") if (repo / ".venv").exists() else sys.executable
            branch_made, note = prepare_branch(repo, day, verdict["edits"], str(verdict.get("regression_test", "")), python)
            message += f" {note}"
            if branch_made:
                ok, why = auto_merge_ok(repo, branch_made)
                if ok:  # a repeat is merged too; the failing-then-passing test and the guard still apply
                    applied = apply_branch(repo, branch_made)
                    message += (f" Applied automatically (all gates passed) and the edition was rerun. {applied} "
                                f"Undo: git revert the merge of {branch_made}.")
                else:
                    message += (f" Needs your OK ({why}): scripts\\approve_doctor.ps1 -Branch {branch_made}")
    repeats = 0
    if not args.dry_run:
        repeats = record_incident(repo, day, incident_key(cause, log_text), branch_made)
    if repeats:
        message = f"REPEAT FAILURE (seen {repeats + 1} times). " + message + " A repeat needs a deterministic rule, not another patch."
    message = redact(message)[:900]
    print(message)
    if not args.dry_run:
        send_telegram(message, critical=bool(repeats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
