"""The local newsroom dashboard: http://127.0.0.1:8765 (this computer only).

Streams .runtime/live/events.jsonl (written by newsroom_live.py) to the page as Server-Sent Events and
serves a small status snapshot. Read-only: it never changes the newsroom.

    pythonw newsroom_dashboard.py            (started at logon by the newsroom-dashboard task)
    python newsroom_dashboard.py --open      (and open it in the browser)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import requests

import newsroom_live

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "dashboard" / "index.html"
PORT = 8765
EASTERN = ZoneInfo("America/New_York")
REPLAY_LIMIT = 20_000  # events sent when a page connects mid-run (the current run only)
SCHEDULE = [
    ("04:00", "Regulatory tracker refresh", "daily"),
    ("04:30", "Edition run", "weekdays"),
    ("every 15 min", "Ensure task: retries a failed edition (up to 4)", "weekdays"),
    ("08:15", "The doctor examines a missing edition", "weekdays"),
]


def read_events_from_last_run(path: Path) -> tuple[list[str], int]:
    """Lines of the current (latest) run and the file offset to tail from."""
    if not path.exists():
        return [], 0
    data = path.read_bytes()
    marker = data.rfind(b'"kind": "run-start"')
    start = data.rfind(b"\n", 0, marker) + 1 if marker >= 0 else 0
    lines = data[start:].decode("utf-8", errors="replace").splitlines()
    return lines[-REPLAY_LIMIT:], len(data)


def next_edition(now: datetime) -> datetime:
    candidate = now.replace(hour=4, minute=30, second=0, microsecond=0)
    while candidate <= now or candidate.weekday() >= 5:
        candidate = (candidate + timedelta(days=1)).replace(hour=4, minute=30)
    return candidate


def service(url: str) -> bool:
    try:
        return requests.get(url, timeout=1.5).status_code < 500
    except requests.RequestException:
        return False


def gpu() -> dict[str, Any]:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip().splitlines()[0]
        name, util, used, total, temp = [part.strip() for part in out.split(",")]
        return {"name": name, "util": int(util), "mem_used": int(used), "mem_total": int(total), "temp": int(temp)}
    except Exception:  # noqa: BLE001 - no NVIDIA GPU or driver tools
        return {}


def loaded_models() -> list[str]:
    try:
        return [m.get("name", "") for m in requests.get("http://127.0.0.1:11434/api/ps", timeout=1.5).json().get("models", [])]
    except Exception:  # noqa: BLE001
        return []


def last_edition() -> dict[str, Any]:
    try:
        briefing = json.loads((ROOT / "data" / "latest_briefing.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    sections = {name: [{"title": s.get("title", ""), "source": s.get("source", ""), "url": s.get("url", "")} for s in items]
                for name, items in (briefing.get("sections") or {}).items() if items}
    date = str(briefing.get("date") or briefing.get("generated_at", ""))[:10]
    qa: dict[str, Any] = {}
    try:
        qa = json.loads((ROOT / "qa" / f"{date}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return {
        "date": date, "generated_at": briefing.get("generated_at", ""), "sections": sections,
        "stories": sum(len(v) for v in sections.values()),
        "candidates": briefing.get("raw_automated_candidate_count") or briefing.get("candidate_count"),
        "qa_summary": qa.get("summary", {}), "qa_notes": qa.get("notes", {}),
        "executive_summary": str(briefing.get("executive_summary", ""))[:2500],
    }


def improvements(limit: int = 14) -> list[dict[str, str]]:
    """The newsroom's change log, newest first: session headings in docs/progress.md and the doctor's merged fixes."""
    items: list[dict[str, str]] = []
    try:
        text = (ROOT / "docs" / "progress.md").read_text(encoding="utf-8")
        for match in re.finditer(r"^## (.+)\n+(.+)", text, re.M):
            heading, first = match.group(1).strip(), match.group(2).strip()
            items.append({"when": heading.split(":")[0], "title": heading.split(":", 1)[-1].strip(), "text": first[:300], "by": "Claude"})
    except OSError:
        pass
    items.reverse()
    try:
        for line in (ROOT / ".runtime" / "incidents.jsonl").read_text(encoding="utf-8").splitlines()[-20:]:
            incident = json.loads(line)
            if incident.get("branch"):
                items.insert(0, {"when": incident.get("day", ""), "title": "Doctor fix", "text": str(incident.get("key", ""))[:200],
                                 "by": "Doctor"})
    except (OSError, ValueError):
        pass
    return items[:limit]


def incidents(limit: int = 8) -> list[dict[str, Any]]:
    try:
        lines = (ROOT / ".runtime" / "incidents.jsonl").read_text(encoding="utf-8").splitlines()[-limit:]
        return [json.loads(line) for line in reversed(lines)]
    except (OSError, ValueError):
        return []


def state() -> dict[str, Any]:
    now = datetime.now(EASTERN)
    nxt = next_edition(now)
    return {
        "now": now.isoformat(), "next_edition": nxt.isoformat(),
        "schedule": [{"at": a, "what": w, "days": d} for a, w, d in SCHEDULE],
        "services": {"ollama": service("http://127.0.0.1:11434/api/version"), "searxng": service("http://localhost:8080/healthz"),
                     "site": None},
        "models": loaded_models(), "gpu": gpu(),
        "writer": "qwen3.8:27b-q4_K_M", "verifier": "gemma4:31b",
        "last_edition": last_edition(), "improvements": improvements(), "incidents": incidents(),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # quiet
        pass

    def send_body(self, body: bytes, kind: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            self.send_body(PAGE.read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/api/state":
            self.send_body(json.dumps(state(), default=str).encode("utf-8"), "application/json")
        elif url.path == "/events":
            self.stream(parse_qs(url.query))
        else:
            self.send_body(b"not found", "text/plain", 404)

    def stream(self, query: dict[str, list[str]]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        path = newsroom_live.events_path()
        lines, offset = read_events_from_last_run(path)
        try:
            self.wfile.write(b"event: replay-start\ndata: {}\n\n")
            for line in lines:
                self.wfile.write(b"data: " + line.encode("utf-8") + b"\n\n")
            self.wfile.write(b"event: replay-end\ndata: {}\n\n")
            self.wfile.flush()
            idle = 0.0
            while True:
                time.sleep(0.25)
                size = path.stat().st_size if path.exists() else 0
                if size < offset:  # rotated
                    offset = 0
                if size > offset:
                    with path.open("rb") as handle:
                        handle.seek(offset)
                        chunk = handle.read(size - offset)
                    complete = chunk.rfind(b"\n") + 1
                    offset += complete
                    for line in chunk[:complete].splitlines():
                        if line.strip():
                            self.wfile.write(b"data: " + line + b"\n\n")
                    self.wfile.flush()
                    idle = 0.0
                else:
                    idle += 0.25
                    if idle >= 15:
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                        idle = 0.0
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        # Already running (the logon task): just open it if asked.
        if args.open:
            webbrowser.open(f"http://127.0.0.1:{args.port}/")
        return 0
    server.daemon_threads = True
    if args.open:
        webbrowser.open(f"http://127.0.0.1:{args.port}/")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
