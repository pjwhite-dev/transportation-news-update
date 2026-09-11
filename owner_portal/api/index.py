from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from copy import deepcopy
from datetime import datetime, timezone
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen


DEFAULT_REPOSITORY = "pjwhite-dev/transportation-news-update"
DEFAULT_BRANCH = "main"
MAX_REQUEST_BYTES = 1_500_000
SESSION_SECONDS = 8 * 60 * 60

SECTION_ORDER = [
    "Trump Administration Wins",
    "Top Developments",
    "UAS and Drones",
    "UAS Security and C-UAS",
    "Military",
    "eVTOL Integration Pilot Program and AAM",
    "Autonomous Vehicles",
    "Other Advanced Transportation",
    "International",
    "Federal Actions",
]

EXECUTIVE_SUMMARY_PROCESS_MARKERS = (
    "supplemental",
    "automated feed",
    "automated record",
    "required item",
    "editorial pass",
    "editorial process",
    "source record",
    "record id",
    "article id",
    "link accounting",
    "coverage accounting",
    "links extracted",
    "links represented",
    "qualifies as a win",
    "qualify as a win",
    "win eligibility",
    "win criteria",
    "win test",
)


class OwnerPortalError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _required_secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise OwnerPortalError("Owner access is not configured.", 503)
    return value


def _session_secret() -> str:
    value = _required_secret("SESSION_SECRET")
    if len(value) < 32:
        raise OwnerPortalError("Owner access is not configured securely.", 503)
    return value


def _owner_password() -> str:
    value = _required_secret("OWNER_PASSWORD")
    if len(value) < 12:
        raise OwnerPortalError("Owner access is not configured securely.", 503)
    return value


def _session_signature(expires: int, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        f"v1.{expires}".encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def create_session_cookie(
    now: int | None = None,
    *,
    secure: bool = True,
) -> str:
    issued = int(time.time() if now is None else now)
    expires = issued + SESSION_SECONDS
    signature = _session_signature(expires, _session_secret())
    value = f"v1.{expires}.{signature}"
    cookie = (
        f"owner_session={value}; Path=/; Max-Age={SESSION_SECONDS}; "
        "HttpOnly; SameSite=Strict"
    )
    return cookie + ("; Secure" if secure else "")


def session_is_valid(cookie_header: str, now: int | None = None) -> bool:
    cookies: dict[str, str] = {}
    for piece in str(cookie_header or "").split(";"):
        name, separator, value = piece.strip().partition("=")
        if separator:
            cookies[name] = value
    value = cookies.get("owner_session", "")
    try:
        version, raw_expires, signature = value.split(".", 2)
        expires = int(raw_expires)
    except (ValueError, TypeError):
        return False
    current = int(time.time() if now is None else now)
    if version != "v1" or expires <= current or expires > current + SESSION_SECONDS + 60:
        return False
    try:
        expected = _session_signature(expires, _session_secret())
    except OwnerPortalError:
        return False
    return hmac.compare_digest(signature, expected)


def _clean_string(value: Any, label: str, maximum: int, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise OwnerPortalError(f"{label} must be text.")
    cleaned = value.strip()
    if required and not cleaned:
        raise OwnerPortalError(f"{label} is required.")
    if len(cleaned) > maximum:
        raise OwnerPortalError(f"{label} is too long.")
    return cleaned


def _validate_story(item: Any, section: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise OwnerPortalError(f"A story in {section} is invalid.")
    story = deepcopy(item)
    story["title"] = _clean_string(
        story.get("title"), f"Headline in {section}", 600, required=True
    )
    story["summary"] = _clean_string(
        story.get("summary"), f"Summary for {story['title']}", 8_000
    )
    url = _clean_string(story.get("url"), f"Link for {story['title']}", 4_000)
    if url and not url.startswith(("https://", "http://")):
        raise OwnerPortalError(f"The link for {story['title']} is invalid.")
    story["url"] = url
    for key, label, maximum in (
        ("win_explanation", "Administration Win explanation", 5_000),
        ("innovative_uas_use", "Innovative UAS use", 1_000),
        ("source", "Source", 500),
        ("date_label", "Date", 200),
    ):
        if key in story:
            story[key] = _clean_string(story.get(key), label, maximum)
    return story


def validate_briefing(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OwnerPortalError("The edition is invalid.")
    briefing = deepcopy(value)
    summary = _clean_string(
        briefing.get("executive_summary"), "Executive Summary", 8_000, required=True
    )
    lowered = summary.casefold()
    if any(marker in lowered for marker in EXECUTIVE_SUMMARY_PROCESS_MARKERS):
        raise OwnerPortalError(
            "The Executive Summary contains intake or editorial-process language."
        )
    briefing["executive_summary"] = summary

    sections = briefing.get("sections")
    if not isinstance(sections, dict):
        raise OwnerPortalError("The edition sections are invalid.")
    unexpected = set(sections) - set(SECTION_ORDER)
    if unexpected:
        raise OwnerPortalError("The edition contains an unsupported section.")
    normalized_sections: dict[str, list[dict[str, Any]]] = {}
    for section in SECTION_ORDER:
        items = sections.get(section, [])
        if not isinstance(items, list) or len(items) > 100:
            raise OwnerPortalError(f"The {section} section is invalid.")
        normalized_sections[section] = [_validate_story(item, section) for item in items]
    briefing["sections"] = normalized_sections

    watch = briefing.get("what_to_watch", [])
    if not isinstance(watch, list) or len(watch) > 30:
        raise OwnerPortalError("What to Watch is invalid.")
    briefing["what_to_watch"] = [
        _clean_string(item, "What to Watch item", 2_000, required=True)
        for item in watch
    ]
    tracker = briefing.get("regulatory_tracker", [])
    if not isinstance(tracker, list) or len(tracker) > 40 or any(
        not isinstance(item, dict) for item in tracker
    ):
        raise OwnerPortalError("The regulatory tracker is invalid.")

    for key in ("window_end", "generated_at"):
        try:
            datetime.fromisoformat(str(briefing.get(key, "")))
            break
        except ValueError:
            continue
    else:
        raise OwnerPortalError("The edition date is invalid.")

    briefing["edition_kind"] = "editorial"
    briefing["publication_mode"] = "owner-edited"
    briefing["owner_edited_at"] = datetime.now(timezone.utc).isoformat()
    return briefing


def briefing_date(briefing: dict[str, Any]) -> str:
    for key in ("window_end", "generated_at"):
        try:
            return datetime.fromisoformat(str(briefing.get(key, ""))).date().isoformat()
        except ValueError:
            continue
    raise OwnerPortalError("The edition date is invalid.")


def _github_json(method: str, path: str, payload: Any | None = None) -> dict[str, Any]:
    token = _required_secret("GITHUB_CONTENT_TOKEN")
    repository = os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPOSITORY).strip()
    url = f"https://api.github.com/repos/{repository}{path}"
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "AdvancedTransportationOwnerPortal/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("message", "")
        except (ValueError, UnicodeDecodeError):
            pass
        raise OwnerPortalError(
            "GitHub could not update the edition"
            + (f": {detail}" if detail else "."),
            409 if exc.code in (409, 422) else 502,
        ) from exc
    except (URLError, TimeoutError, ValueError) as exc:
        raise OwnerPortalError("GitHub could not be reached.", 502) from exc


def load_latest_briefing() -> dict[str, Any]:
    branch = quote(os.environ.get("GITHUB_BRANCH", DEFAULT_BRANCH).strip(), safe="")
    result = _github_json(
        "GET", f"/contents/data/latest_briefing.json?ref={branch}"
    )
    try:
        raw = base64.b64decode(result["content"], validate=False)
        payload = json.loads(raw.decode("utf-8"))
    except (KeyError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OwnerPortalError("The current edition could not be read.", 502) from exc
    if not isinstance(payload, dict):
        raise OwnerPortalError("The current edition is invalid.", 502)
    return payload


def commit_briefing(briefing: dict[str, Any], attempts: int = 3) -> str:
    branch_name = os.environ.get("GITHUB_BRANCH", DEFAULT_BRANCH).strip()
    branch = quote(branch_name, safe="")
    content = json.dumps(briefing, ensure_ascii=False, indent=2) + "\n"
    day = briefing_date(briefing)
    last_error: OwnerPortalError | None = None
    for _attempt in range(max(1, attempts)):
        try:
            ref = _github_json("GET", f"/git/ref/heads/{branch}")
            parent = ref["object"]["sha"]
            commit = _github_json("GET", f"/git/commits/{parent}")
            blob = _github_json(
                "POST", "/git/blobs", {"content": content, "encoding": "utf-8"}
            )
            tree = _github_json(
                "POST",
                "/git/trees",
                {
                    "base_tree": commit["tree"]["sha"],
                    "tree": [
                        {
                            "path": "data/latest_briefing.json",
                            "mode": "100644",
                            "type": "blob",
                            "sha": blob["sha"],
                        },
                        {
                            "path": f"data/archive/{day}.json",
                            "mode": "100644",
                            "type": "blob",
                            "sha": blob["sha"],
                        },
                    ],
                },
            )
            created = _github_json(
                "POST",
                "/git/commits",
                {
                    "message": f"Owner edit news edition {day}",
                    "tree": tree["sha"],
                    "parents": [parent],
                },
            )
            _github_json(
                "PATCH",
                f"/git/refs/heads/{branch}",
                {"sha": created["sha"], "force": False},
            )
            return created["sha"]
        except (KeyError, OwnerPortalError) as exc:
            last_error = exc if isinstance(exc, OwnerPortalError) else OwnerPortalError(
                "GitHub returned an incomplete response.", 502
            )
            if last_error.status != 409:
                raise last_error
    raise OwnerPortalError(
        "The edition changed while it was being saved. Please reload and try again.",
        409,
    ) from last_error


class handler(BaseHTTPRequestHandler):
    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        cookie: str | None = None,
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise OwnerPortalError("The request is invalid.") from exc
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise OwnerPortalError("The request is empty or too large.")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OwnerPortalError("The request is invalid.") from exc
        if not isinstance(payload, dict):
            raise OwnerPortalError("The request is invalid.")
        return payload

    def _require_session(self) -> None:
        if not session_is_valid(self.headers.get("Cookie", "")):
            raise OwnerPortalError("Please sign in again.", 401)

    def _require_same_origin(self) -> None:
        origin = self.headers.get("Origin", "")
        host = self.headers.get("Host", "")
        if not origin or urlparse(origin).netloc.casefold() != host.casefold():
            raise OwnerPortalError("The request origin is invalid.", 403)

    def do_GET(self) -> None:
        action = parse_qs(urlparse(self.path).query).get("action", [""])[0]
        try:
            if action == "session":
                self._send_json(
                    200,
                    {"authenticated": session_is_valid(self.headers.get("Cookie", ""))},
                )
                return
            if action == "edition":
                self._require_session()
                self._send_json(200, {"briefing": load_latest_briefing()})
                return
            raise OwnerPortalError("Not found.", 404)
        except OwnerPortalError as exc:
            self._send_json(exc.status, {"error": str(exc)})

    def do_POST(self) -> None:
        action = parse_qs(urlparse(self.path).query).get("action", [""])[0]
        try:
            self._require_same_origin()
            if action == "login":
                password = _clean_string(
                    self._read_json().get("password"), "Password", 1_000
                )
                if not hmac.compare_digest(password, _owner_password()):
                    time.sleep(0.75)
                    raise OwnerPortalError("Incorrect password.", 401)
                self._send_json(
                    200,
                    {"authenticated": True},
                    cookie=create_session_cookie(),
                )
                return
            if action == "logout":
                self._send_json(
                    200,
                    {"authenticated": False},
                    cookie=(
                        "owner_session=; Path=/; Max-Age=0; HttpOnly; Secure; "
                        "SameSite=Strict"
                    ),
                )
                return
            if action == "save":
                self._require_session()
                briefing = validate_briefing(self._read_json().get("briefing"))
                commit_sha = commit_briefing(briefing)
                self._send_json(
                    200,
                    {
                        "saved": True,
                        "commit": commit_sha,
                        "message": (
                            "Saved. The public edition will refresh in a few minutes."
                        ),
                    },
                )
                return
            raise OwnerPortalError("Not found.", 404)
        except OwnerPortalError as exc:
            self._send_json(exc.status, {"error": str(exc)})
