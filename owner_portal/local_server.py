from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import os
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from api.index import (
    briefing_date,
    create_session_cookie,
    session_is_valid,
    validate_briefing,
)


def run(command: list[str], root: Path) -> str:
    result = subprocess.run(
        command,
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode:
        detail = " ".join((result.stderr or result.stdout).split())[:1000]
        raise RuntimeError(f"{' '.join(command[:2])} failed: {detail}")
    return result.stdout.strip()


def atomic_write(path: Path, payload: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def save_local_briefing(
    root: Path,
    value: Any,
    *,
    publish: bool = True,
) -> tuple[dict[str, Any], str]:
    briefing = validate_briefing(value)
    latest = root / "data" / "latest_briefing.json"
    archive = root / "data" / "archive" / f"{briefing_date(briefing)}.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if publish:
        dirty = run(["git", "status", "--porcelain"], root)
        if dirty:
            raise RuntimeError(
                "The repository has uncommitted changes. Resolve them before an owner edit."
            )
        run(["git", "pull", "--ff-only", "origin", "main"], root)
    payload = json.dumps(briefing, indent=2, ensure_ascii=False) + "\n"
    atomic_write(latest, payload)
    atomic_write(archive, payload)
    python = root / ".venv" / "Scripts" / "python.exe"
    run([str(python), "owner_edit_validation.py", str(latest)], root)
    if not publish:
        return briefing, "Validated local owner edit."
    run(["git", "add", str(latest), str(archive)], root)
    run(
        [
            "git",
            "commit",
            "-m",
            f"Owner edit news edition {briefing_date(briefing)}",
        ],
        root,
    )
    run(["git", "push", "origin", "main"], root)
    sha = run(["git", "rev-parse", "--short", "HEAD"], root)
    return briefing, f"Saved and published owner edit {sha}."


class OwnerEditorHandler(BaseHTTPRequestHandler):
    root: Path
    static_root: Path

    def _json(
        self,
        status: int,
        value: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> None:
        payload = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for name, item in (headers or {}).items():
            self.send_header(name, item)
        self.end_headers()
        self.wfile.write(payload)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 5_000_000:
            raise ValueError("Request body is too large.")
        value = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object.")
        return value

    def _action(self) -> str:
        return parse_qs(urlparse(self.path).query).get("action", [""])[0]

    def _authenticated(self) -> bool:
        try:
            return session_is_valid(self.headers.get("Cookie", ""))
        except RuntimeError:
            return False

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin", "")
        host = self.headers.get("Host", "")
        return origin in {f"http://{host}", f"https://{host}"}

    def do_GET(self) -> None:
        if self.path.startswith("/api"):
            action = self._action()
            if action == "session":
                self._json(HTTPStatus.OK, {"authenticated": self._authenticated()})
            elif action == "edition" and self._authenticated():
                try:
                    briefing = json.loads(
                        (self.root / "data" / "latest_briefing.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    self._json(HTTPStatus.OK, {"briefing": briefing})
                except Exception as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            elif action == "edition":
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "Sign in required."})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown API action."})
            return
        relative = urlparse(self.path).path.lstrip("/") or "index.html"
        candidate = (self.static_root / relative).resolve()
        if self.static_root not in candidate.parents and candidate != self.static_root:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type",
            mimetypes.guess_type(candidate.name)[0] or "application/octet-stream",
        )
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        action = self._action()
        if not self.path.startswith("/api") or not self._same_origin():
            self._json(HTTPStatus.FORBIDDEN, {"error": "Same-origin request required."})
            return
        if action == "login":
            try:
                supplied = str(self._body().get("password", ""))
                expected = os.environ.get("OWNER_PASSWORD", "")
                if len(expected) < 12 or not hmac.compare_digest(supplied, expected):
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "Invalid password."})
                    return
                self._json(
                    HTTPStatus.OK,
                    {"authenticated": True},
                    {"Set-Cookie": create_session_cookie(secure=False)},
                )
            except Exception as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if action == "logout":
            self._json(
                HTTPStatus.OK,
                {"authenticated": False},
                {
                    "Set-Cookie": (
                        "owner_session=; Path=/; Max-Age=0; HttpOnly; "
                        "SameSite=Strict"
                    )
                },
            )
            return
        if action != "save":
            self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown API action."})
            return
        if not self._authenticated():
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "Sign in required."})
            return
        try:
            request = self._body()
            _, message = save_local_briefing(self.root, request.get("briefing"))
            self._json(HTTPStatus.OK, {"message": message})
        except Exception as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the authenticated local owner editor.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("The local owner editor may bind only to a loopback address.")
    if len(os.environ.get("OWNER_PASSWORD", "")) < 12:
        raise ValueError("OWNER_PASSWORD must contain at least 12 characters.")
    if len(os.environ.get("SESSION_SECRET", "")) < 32:
        raise ValueError("SESSION_SECRET must contain at least 32 characters.")
    OwnerEditorHandler.root = args.root.resolve()
    OwnerEditorHandler.static_root = Path(__file__).resolve().parent
    server = ThreadingHTTPServer((args.host, args.port), OwnerEditorHandler)
    print(f"Owner editor listening on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
