"""Git hygiene before a production edition run.

The pipeline regenerates a few tracked files on every run (tracker ledger, market snapshot, raw feed, published
groups). Uncommitted changes to just those are discarded here; anything else dirty stops the run. This lives in
Python, not in the PowerShell script, so it is unit-tested against a real temporary repository and the doctor can
repair it: a PowerShell ``git checkout -- a b c`` aborts the whole command (and the whole run) when any one path
is not tracked, which is how the 2026-10-28 edition failed.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

# Tracked files that every run rewrites; local changes to them are never worth keeping.
REGENERATED = (
    "tracker/events.jsonl",
    "tracker/verifications.jsonl",
    "tracker/oira.json",
    "tracker/discovered.yaml",
    "data/market.json",
    "data/published_groups.json",
    "data/latest_raw_news.json",
)
# Untracked files a run may leave behind that must not stop the next one.
TOLERATED_UNTRACKED = (r"qa/", r"tracker/discovered\.yaml$")


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")


def discard_regenerated(repo: Path, today: str) -> list[str]:
    """Restore each regenerated file that git tracks and has changed; paths git does not know are skipped.

    Today's raw archive is rewritten by every run; once the 04:15 GitHub collection has committed it, a failed
    local run leaves it modified (2026-10-28 evening: the retry refused to start because of it).
    """
    discarded: list[str] = []
    for path in (*REGENERATED, f"data/raw_archive/{today}.json"):
        if git(repo, "ls-files", "--error-unmatch", "--", path).returncode != 0:
            continue
        if git(repo, "status", "--porcelain", "--", path).stdout.strip():
            if git(repo, "checkout", "--", path).returncode == 0:
                discarded.append(path)
    return discarded


def dirty_files(repo: Path, today: str) -> list[str]:
    """Porcelain lines that should stop a production run."""
    todays_raw = f"data/raw_archive/{today}.json"
    tolerated = [re.compile(r"^\?\? " + pattern) for pattern in TOLERATED_UNTRACKED]
    blocked = []
    for line in git(repo, "status", "--porcelain").stdout.splitlines():
        if line.strip() == f"?? {todays_raw}" or any(rule.match(line) for rule in tolerated):
            continue
        blocked.append(line.strip())
    return blocked


def preflight(repo: Path, today: str | None = None) -> tuple[bool, str]:
    today = today or datetime.now(EASTERN).date().isoformat()
    discarded = discard_regenerated(repo, today)
    blocked = dirty_files(repo, today)
    if blocked:
        return False, "Refusing production run because the repository is not clean: " + "; ".join(blocked)
    return True, ("Discarded regenerated files: " + ", ".join(discarded)) if discarded else "Repository is clean."


def main() -> int:
    ok, message = preflight(Path(__file__).resolve().parent)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
