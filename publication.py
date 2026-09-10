from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

import requests


DEFAULT_REPOSITORY = "pjwhite-dev/transportation-news-update"
DEFAULT_BRANCH = "main"
_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def briefing_date(briefing: dict[str, Any]) -> str:
    for key in ("window_end", "generated_at"):
        value = str(briefing.get(key, ""))
        try:
            return datetime.fromisoformat(value).date().isoformat()
        except ValueError:
            continue
    raise ValueError("The briefing has no valid edition date.")


def briefing_payload(briefing: dict[str, Any]) -> str:
    return json.dumps(briefing, indent=2, ensure_ascii=False) + "\n"


def normalize_repository(value: str) -> str:
    repository = (value or DEFAULT_REPOSITORY).strip()
    if not _REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError("GitHub repository must use the owner/repository format.")
    return repository


def _request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = session.request(
        method,
        url,
        headers=headers,
        json=payload,
        timeout=30,
    )
    if response.status_code >= 400:
        try:
            detail = response.json().get("message", "")
        except ValueError:
            detail = ""
        raise RuntimeError(
            f"GitHub publishing failed ({response.status_code})"
            + (f": {detail}" if detail else ".")
        )
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("GitHub returned an invalid publishing response.") from exc


def publish_briefing_to_github(
    briefing: dict[str, Any],
    token: str,
    repository: str = DEFAULT_REPOSITORY,
    branch: str = DEFAULT_BRANCH,
    *,
    session: requests.Session | None = None,
    attempts: int = 3,
) -> str:
    """Commit the latest briefing and its dated archive in one GitHub commit."""
    if not token.strip():
        raise ValueError("GitHub publishing is not configured.")
    repository = normalize_repository(repository)
    branch = branch.strip() or DEFAULT_BRANCH
    client = session or requests.Session()
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token.strip()}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "AdvancedTransportationNewsPublisher/1.0",
    }
    api = f"https://api.github.com/repos/{repository}"
    content = briefing_payload(briefing)
    date_label = briefing_date(briefing)
    encoded_branch = quote(branch, safe="")

    last_error: Exception | None = None
    for _ in range(max(1, attempts)):
        try:
            ref = _request_json(
                client,
                "GET",
                f"{api}/git/ref/heads/{encoded_branch}",
                headers=headers,
            )
            parent_sha = ref["object"]["sha"]
            commit = _request_json(
                client,
                "GET",
                f"{api}/git/commits/{parent_sha}",
                headers=headers,
            )
            base_tree = commit["tree"]["sha"]
            blob = _request_json(
                client,
                "POST",
                f"{api}/git/blobs",
                headers=headers,
                payload={"content": content, "encoding": "utf-8"},
            )
            tree = _request_json(
                client,
                "POST",
                f"{api}/git/trees",
                headers=headers,
                payload={
                    "base_tree": base_tree,
                    "tree": [
                        {
                            "path": "data/latest_briefing.json",
                            "mode": "100644",
                            "type": "blob",
                            "sha": blob["sha"],
                        },
                        {
                            "path": f"data/archive/{date_label}.json",
                            "mode": "100644",
                            "type": "blob",
                            "sha": blob["sha"],
                        },
                    ],
                },
            )
            new_commit = _request_json(
                client,
                "POST",
                f"{api}/git/commits",
                headers=headers,
                payload={
                    "message": f"Publish news edition {date_label}",
                    "tree": tree["sha"],
                    "parents": [parent_sha],
                },
            )
            _request_json(
                client,
                "PATCH",
                f"{api}/git/refs/heads/{encoded_branch}",
                headers=headers,
                payload={"sha": new_commit["sha"], "force": False},
            )
            return new_commit["sha"]
        except (KeyError, RuntimeError, requests.RequestException) as exc:
            last_error = exc
            if isinstance(exc, RuntimeError) and "(422)" not in str(exc) and "(409)" not in str(exc):
                break
    raise RuntimeError("Unable to publish the edition after retrying.") from last_error
