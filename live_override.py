"""The owner's headline override, requested through GitHub.

The /login page (site_assets/login) opens a pre-filled GitHub issue; the owner submits it while signed in to
GitHub. This worker reads open issues titled "Headline override" that the repository owner opened, applies
each one through Desk.force_lead, then answers and closes it. GitHub's sign-in is the login: an issue from
anyone else is ignored and never changes the page. The token comes from the keyring (alerts.get_secret).

Issue body, as the page writes it:
    story: <story id>
    hours: <1-48, or 0 to end the override now>
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import requests

import newsroom_live
from alerts import get_secret

API = "https://api.github.com"
TITLE = "Headline override"
DONE_KEY = "override_issues_done"  # issue numbers already handled, in case closing one fails

# A story id is the wire's hex id or, for a story stored without one, its address key (live_store.url_key).
_STORY = re.compile(r"^\s*story:\s*(\S{4,300})\s*$", re.M)
_HOURS = re.compile(r"^\s*hours:\s*(\d{1,2})\s*$", re.M)


def parse_request(body: str) -> tuple[str, int] | None:
    """(story id, hours) from an issue body, or None when it is not a well-formed request."""
    story, hours = _STORY.search(body or ""), _HOURS.search(body or "")
    if not story or not hours:
        return None
    return story.group(1), int(hours.group(1))


class OverrideDesk:
    def __init__(
        self,
        desk: Any,
        config: dict[str, Any],
        *,
        session: requests.Session | None = None,
        token: Callable[[], str | None] = lambda: get_secret("github_token"),
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        settings = config["override"]
        self.desk, self.repo, self.owner = desk, str(settings["repo"]), str(settings["owner"])
        self.every = float(settings.get("every_seconds", 60))
        self.max_age = timedelta(hours=float(settings.get("max_request_age_hours", 6)))
        self.session, self.token, self.clock = session or requests.Session(), token, clock
        self.error = ""

    def _headers(self) -> dict[str, str]:
        key = self.token()
        if not key:
            raise RuntimeError("no github_token stored (scripts/set_secret.py github_token)")
        return {"Authorization": f"Bearer {key}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

    def _answer(self, number: int, text: str, headers: dict[str, str]) -> None:
        base = f"{API}/repos/{self.repo}/issues/{number}"
        try:
            self.session.post(f"{base}/comments", json={"body": text}, headers=headers, timeout=20).raise_for_status()
            self.session.patch(base, json={"state": "closed", "state_reason": "completed"}, headers=headers, timeout=20).raise_for_status()
        except requests.RequestException as exc:
            self.error = f"could not answer issue #{number}: {type(exc).__name__}"

    def check(self) -> int:
        """Handle open override requests; returns how many were applied. Never raises."""
        try:
            headers = self._headers()
            response = self.session.get(
                f"{API}/repos/{self.repo}/issues",
                params={"state": "open", "creator": self.owner, "per_page": 20, "sort": "created", "direction": "asc"},
                headers=headers, timeout=20,
            )
            response.raise_for_status()
            issues = response.json()
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            self.error = f"GitHub check failed: {exc if isinstance(exc, RuntimeError) else type(exc).__name__}"
            return 0
        self.error = ""
        done = set(self.desk.store.get_state(DONE_KEY, []) or [])
        applied = 0
        now = self.clock()
        for issue in issues:
            number = int(issue.get("number", 0))
            if (not str(issue.get("title", "")).startswith(TITLE) or "pull_request" in issue or number in done
                    or str((issue.get("user") or {}).get("login", "")).casefold() != self.owner.casefold()):
                continue
            done.add(number)
            created = datetime.fromisoformat(str(issue.get("created_at", "")).replace("Z", "+00:00")) if issue.get("created_at") else now
            request = parse_request(str(issue.get("body") or ""))
            if request is None:
                reply = "Not applied: the request needs a `story:` line and an `hours:` line (1 to 48, or 0 to end)."
            elif now - created > self.max_age:
                reply = "Not applied: this request is more than a few hours old. Make a new one from the /login page."
            else:
                story_id, hours = request
                try:
                    result = self.desk.force_lead(story_id, hours, note=f"(issue #{number})")
                    reply, applied = f"Done: {result}.", applied + 1
                except ValueError as exc:
                    reply = f"Not applied: {exc}."
            newsroom_live.emit("live-override-request", desk="editors", issue=number, reply=reply[:200])
            self._answer(number, reply, headers)
        self.desk.store.set_state(DONE_KEY, sorted(done)[-200:])
        return applied

    def run(self, stop: threading.Event, beat: Callable[[], None] = lambda: None) -> None:
        while not stop.is_set():
            beat()
            self.check()
            stop.wait(self.every)
