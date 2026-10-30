"""Deliver feed.json to the public site: commit it to the live-feed branch and push (never force).

The page at news.peterjwhite.org/live/ (GitHub Pages) reads the file from raw.githubusercontent.com, so
the reader page and the feed are both served by GitHub and no new service is exposed. A push goes out when
the stories changed (at most every `min_seconds`) and at least every `heartbeat_seconds`, so the page can
tell readers when updates have stopped. The branch has its own worktree beside this one.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import live_feed
import newsroom_live

BRANCH = "live-feed"


def git(args: list[str], cwd: Path, timeout: float = 90) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)


class FeedPusher:
    def __init__(
        self,
        config: dict[str, Any],
        out_dir: Path,
        repo_root: Path,
        *,
        run: Callable[[list[str], Path], subprocess.CompletedProcess[str]] = git,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        settings = config["push"]
        self.enabled = bool(settings.get("enabled", False))
        self.worktree = Path(settings["worktree"])
        self.min_gap = timedelta(seconds=float(settings.get("min_seconds", 180)))
        self.heartbeat = timedelta(seconds=float(settings.get("heartbeat_seconds", 600)))
        self.out_dir, self.repo_root, self.run_git, self.clock = out_dir, repo_root, run, clock
        self.last_push: datetime | None = None
        self.last_hash = ""
        self.error = ""

    def ensure_worktree(self) -> bool:
        if (self.worktree / ".git").exists():
            return True
        has_branch = self.run_git(["rev-parse", "--verify", "--quiet", f"refs/heads/{BRANCH}"], self.repo_root).returncode == 0
        args = ["worktree", "add", str(self.worktree), BRANCH] if has_branch else ["worktree", "add", "--orphan", "-b", BRANCH, str(self.worktree)]
        result = self.run_git(args, self.repo_root)
        if result.returncode != 0:
            self.error = f"could not create the {BRANCH} worktree: {result.stderr.strip()[:200]}"
            return False
        return True

    def due(self, feed_hash: str, now: datetime) -> bool:
        if self.last_push is None:
            return True
        since = now - self.last_push
        return since >= self.heartbeat or (feed_hash != self.last_hash and since >= self.min_gap)

    def push_once(self) -> bool:
        """Push the current feed if one is due; True when a push went out. Never raises."""
        source = self.out_dir / "feed.json"
        now = self.clock()
        try:
            feed = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.error = f"no feed to push: {type(exc).__name__}"
            return False
        feed_hash = live_feed.content_hash(feed)
        if not self.due(feed_hash, now) or not self.ensure_worktree():
            return False
        try:
            shutil.copyfile(source, self.worktree / "feed.json")
            steps = (
                ["add", "feed.json"],
                ["commit", "--quiet", "-m", f"Live feed {now.strftime('%Y-%m-%d %H:%M')} UTC"],
                ["push", "--quiet", "origin", f"{BRANCH}:{BRANCH}"],
            )
            for step in steps:
                result = self.run_git(step, self.worktree)
                if result.returncode != 0 and not (step[0] == "commit" and "nothing to commit" in result.stdout + result.stderr):
                    raise RuntimeError(f"git {step[0]} failed: {(result.stderr or result.stdout).strip()[:200]}")
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.error = str(exc)[:300]
            newsroom_live.emit("live-push-fail", desk="press", error=self.error)
            return False
        self.last_push, self.last_hash, self.error = now, feed_hash, ""
        newsroom_live.emit("live-push", desk="press", stories=len(feed.get("stories", [])))
        return True

    def run(self, stop: threading.Event, beat: Callable[[], None] = lambda: None) -> None:
        while not stop.is_set():
            beat()
            if self.enabled:
                self.push_once()
            stop.wait(30)
