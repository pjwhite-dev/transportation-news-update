"""The local monitor: http://127.0.0.1:8790 (this computer only).

A live look at everything the newsroom does as it happens: the live site's events and model calls (streamed from
.runtime/live_site/events.jsonl as Server-Sent Events), the GPU, the models Ollama holds, the service's heartbeat and
the scheduled tasks. Its one control is Pause / Resume:

  Pause   writes the GPU pause file (no end), stops and disables the newsroom-live task (the services check leaves a
          disabled task alone) and unloads the newsroom's models from the card.
  Resume  removes the pause file, enables the task and starts it.

    pythonw monitor.py            (started at logon by the newsroom-monitor task)
    python monitor.py --open      (and open it in the browser)

The control answers only same-origin requests on 127.0.0.1 that carry the X-Monitor header, so no web page can press it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests
import yaml

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "monitor" / "index.html"
PORT = 8790
LIVE_DIR = Path(os.environ.get("NEWSROOM_LIVE_DIR") or ROOT / ".runtime" / "live_site")
OLLAMA = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
LIVE_TASK = "newsroom-live"
BACKLOG_BYTES = 3_000_000  # the end of the event log read when a page connects
BACKLOG_EVENTS = 400
SAMPLE_SECONDS = 2
TASK_SECONDS = 20
HISTORY = 300  # GPU samples kept: ten minutes
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

TASKS = {
    "newsroom-live": "Live site: wire, writing, checking, publishing",
    "newsroom-ollama": "Ollama, the model server",
    "newsroom-hermes-gateway": "Hermes gateway",
    "newsroom-wsl": "WSL, for local search",
    "newsroom-services-check": "Watchdog: restarts what stops (every 5 min)",
    "newsroom-heartbeat": "PC-alive ping (every 5 min)",
    "newsroom-monitor": "This monitor",
    "newsroom-edition": "Retired: the daily edition",
    "newsroom-ensure": "Retired: edition retries",
    "newsroom-doctor": "Retired: edition doctor",
    "newsroom-tracker": "Retired: regulatory tracker",
    "newsroom-dashboard": "Retired: the old dashboard",
}

TASKS_SCRIPT = (
    "Get-ScheduledTask -TaskName 'newsroom-*' | ForEach-Object { $i = Get-ScheduledTaskInfo -TaskName $_.TaskName; "
    "[pscustomobject]@{ name = $_.TaskName; state = [string]$_.State; result = $i.LastTaskResult; "
    "last = $(if ($i.LastRunTime) { $i.LastRunTime.ToString('o') } else { '' }); "
    "next = $(if ($i.NextRunTime) { $i.NextRunTime.ToString('o') } else { '' }) } } | ConvertTo-Json -Compress"
)
PAUSE_SCRIPT = (
    f"Stop-ScheduledTask -TaskName {LIVE_TASK} -ErrorAction SilentlyContinue; "
    "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' OR Name='python.exe'\" | "
    "Where-Object { $_.CommandLine -like '*live_service*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; "
    f"Disable-ScheduledTask -TaskName {LIVE_TASK} | Out-Null"
)
RESUME_SCRIPT = f"Enable-ScheduledTask -TaskName {LIVE_TASK} | Out-Null; Start-ScheduledTask -TaskName {LIVE_TASK}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def powershell(script: str, timeout: int = 30) -> tuple[int, str]:
    try:
        done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                              capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
        return done.returncode, (done.stdout + done.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def gpu() -> dict[str, Any]:
    try:
        line = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4, creationflags=NO_WINDOW,
        ).stdout.strip().splitlines()[0]
        name, util, used, total, temp, power, limit = [part.strip() for part in line.split(",")]
        number = lambda text: float(text) if text.replace(".", "", 1).isdigit() else None
        return {"name": name, "util": number(util), "mem_used": number(used), "mem_total": number(total),
                "temp": number(temp), "power": number(power), "power_limit": number(limit)}
    except Exception:  # noqa: BLE001 - no NVIDIA card or driver tools
        return {}


def ollama_models() -> dict[str, Any]:
    try:
        models = requests.get(f"{OLLAMA}/api/ps", timeout=2).json().get("models") or []
    except Exception:  # noqa: BLE001
        return {"up": False, "models": []}
    return {"up": True, "models": [{"name": m.get("name", ""), "vram": m.get("size_vram") or m.get("size") or 0,
                                    "until": m.get("expires_at", "")} for m in models]}


def newsroom_models() -> set[str]:
    """The models the live site loads (config/live.yaml), the only ones Pause unloads."""
    try:
        desk = yaml.safe_load((ROOT / "config" / "live.yaml").read_text(encoding="utf-8"))["desk"]
    except (OSError, KeyError, TypeError, yaml.YAMLError):
        return set()
    return {str(desk[k]) for k in ("writer_model", "fallback_model", "verifier_model") if desk.get(k)}


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def pause_state(pause_path: Path, now: datetime | None = None) -> dict[str, Any]:
    """The pause file as the live service reads it: in force until its "until" time."""
    data = read_json(pause_path) or {}
    try:
        until = datetime.fromisoformat(str(data.get("until", "")))
    except ValueError:
        until = None
    active = until is not None and (now or datetime.now(timezone.utc)) < until
    return {"active": active, "until": data.get("until") if active else None, "why": data.get("why") if active else None,
            "since": data.get("since") if active else None}


def heartbeat(path: Path) -> dict[str, Any] | None:
    beat = read_json(path)
    if beat is None:
        return None
    try:
        beat["age"] = max(0, int(time.time() - path.stat().st_mtime))
    except OSError:
        pass
    return beat


def tail_events(path: Path, limit_bytes: int = BACKLOG_BYTES, keep: int = BACKLOG_EVENTS) -> tuple[list[str], int]:
    """The last `keep` events worth showing (no quiet wire sweeps), every sweep of the last 15 minutes (for the wire
    counter), and the tokens of the newest model call and of the last one to finish, in order; plus the offset to
    stream from."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - limit_bytes))
            chunk = handle.read(size - handle.tell())
    except OSError:
        return [], 0
    lines = chunk.split(b"\n")
    if size > limit_bytes:
        lines = lines[1:]  # the first line is cut
    complete = size - len(lines[-1]) if lines else size
    cutoff = time.time() - 15 * 60
    parsed = []
    for raw in lines[:-1]:
        try:
            parsed.append((json.loads(raw), raw.decode("utf-8", "replace").rstrip("\r")))
        except ValueError:
            continue
    last = lambda kind: next((e.get("id") for e, _ in reversed(parsed) if e.get("kind") == kind), None)
    shown_calls = {last("ink-start"), last("ink-end")} - {None}
    picked = []  # (line, counts toward `keep`)
    for event, line in parsed:
        kind = event.get("kind")
        if kind == "ink":
            if event.get("id") in shown_calls:
                picked.append((line, False))
        elif kind == "live-sweep":
            try:
                fresh = datetime.fromisoformat(event["t"]).timestamp() >= cutoff
            except (KeyError, ValueError):
                fresh = False
            if fresh or int(event.get("new") or 0):
                picked.append((line, not fresh))
        else:
            picked.append((line, True))
    drop = max(0, sum(counted for _, counted in picked) - keep)
    out = []
    for line, counted in picked:
        if counted and drop:
            drop -= 1
            continue
        out.append(line)
    return out, complete


def append_event(path: Path, kind: str, **fields: Any) -> None:
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"t": now_iso(), "kind": kind, "desk": "owner", **fields}) + "\n")
    except OSError:
        pass


class Monitor:
    """Samples the machine in the background and carries out Pause / Resume."""

    def __init__(self, live_dir: Path = LIVE_DIR, run: Callable[[str], tuple[int, str]] = powershell,
                 unload: Callable[[str], None] | None = None) -> None:
        self.live_dir = live_dir
        self.events = live_dir / "events.jsonl"
        self.pause_path = live_dir / "gpu_pause.json"  # live_desk.GPU_PAUSE
        self.run = run
        self.unload = unload or (lambda model: requests.post(f"{OLLAMA}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30))
        self.lock = threading.Lock()
        self.busy = ""
        self.history: deque[list[Any]] = deque(maxlen=HISTORY)
        self.snapshot: dict[str, Any] = {}
        self.tasks: list[dict[str, Any]] = []
        self.version = 0

    # --- sampling ---------------------------------------------------------------------------------------------------
    def refresh_tasks(self) -> None:
        code, out = self.run(TASKS_SCRIPT)
        try:
            rows = json.loads(out) if code == 0 and out else []
        except ValueError:
            rows = []
        rows = [rows] if isinstance(rows, dict) else rows
        tasks = [{**row, "about": TASKS.get(row.get("name", ""), "")} for row in rows if isinstance(row, dict)]
        order = list(TASKS)
        tasks.sort(key=lambda t: (t["about"].startswith("Retired"), order.index(t["name"]) if t["name"] in order else 99))
        with self.lock:
            self.tasks = tasks

    def sample(self) -> None:
        card, models = gpu(), ollama_models()
        if card:
            self.history.append([int(time.time()), card.get("util"), card.get("mem_used")])
        ours = newsroom_models()
        for m in models["models"]:
            m["newsroom"] = m["name"] in ours
        state = {
            "at": now_iso(), "gpu": card, "history": list(self.history), "ollama": models,
            "heartbeat": heartbeat(self.live_dir / "heartbeat.json"), "pause": pause_state(self.pause_path),
        }
        with self.lock:
            state["tasks"] = self.tasks
            live = next((t for t in self.tasks if t.get("name") == LIVE_TASK), {})
            state["paused"] = state["pause"]["active"] or live.get("state") == "Disabled"
            state["service"] = live.get("state", "")
            state["busy"] = self.busy
            self.snapshot = state
            self.version += 1

    def loop(self, stop: threading.Event) -> None:
        last_tasks = 0.0
        while not stop.is_set():
            if time.time() - last_tasks >= TASK_SECONDS:
                self.refresh_tasks()
                last_tasks = time.time()
            self.sample()
            stop.wait(SAMPLE_SECONDS)

    def state(self) -> tuple[int, dict[str, Any]]:
        with self.lock:
            return self.version, dict(self.snapshot)

    # --- the control ------------------------------------------------------------------------------------------------
    def pause(self) -> dict[str, Any]:
        with self.lock:
            if self.busy:
                return {"ok": False, "error": f"already {self.busy}"}
            self.busy = "pausing"
        try:
            self.pause_path.parent.mkdir(parents=True, exist_ok=True)
            self.pause_path.write_text(json.dumps({"until": "2099-01-01T00:00:00+00:00", "since": now_iso(),
                                                   "why": "owner: paused from the local monitor"}) + "\n", encoding="utf-8")
            code, out = self.run(PAUSE_SCRIPT)
            unloaded = []
            ours = newsroom_models()
            for model in ollama_models()["models"]:
                if model["name"] in ours:
                    try:
                        self.unload(model["name"])
                        unloaded.append(model["name"])
                    except Exception:  # noqa: BLE001 - Ollama down: nothing is loaded anyway
                        pass
            append_event(self.events, "owner-pause", unloaded=unloaded, ok=code == 0)
            return {"ok": code == 0, "error": "" if code == 0 else out[-400:], "unloaded": unloaded}
        finally:
            self.finish()

    def resume(self) -> dict[str, Any]:
        with self.lock:
            if self.busy:
                return {"ok": False, "error": f"already {self.busy}"}
            self.busy = "resuming"
        try:
            try:
                self.pause_path.unlink()
            except FileNotFoundError:
                pass
            code, out = self.run(RESUME_SCRIPT)
            append_event(self.events, "owner-resume", ok=code == 0)
            return {"ok": code == 0, "error": "" if code == 0 else out[-400:]}
        finally:
            self.finish()

    def finish(self) -> None:
        with self.lock:
            self.busy = ""
        self.refresh_tasks()
        self.sample()


def allowed(headers: Any, port: int) -> bool:
    """Same-origin requests from this computer only: the Host is ours (no DNS rebinding), any Origin is ours, and the
    X-Monitor header is set (a cross-site page cannot send it without a preflight this server never answers)."""
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origin = headers.get("Origin")
    return (headers.get("Host") in hosts and headers.get("X-Monitor") == "1"
            and (origin is None or origin in {f"http://{h}" for h in hosts}))


def make_handler(monitor: Monitor, port: int) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # quiet
            pass

        def send_body(self, body: bytes, kind: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                return self.send_body(b"forbidden", "text/plain", 403)
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self.send_body(PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/state":
                self.send_body(json.dumps(monitor.state()[1], default=str).encode("utf-8"), "application/json")
            elif path == "/api/stream":
                self.stream()
            else:
                self.send_body(b"not found", "text/plain", 404)

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path not in ("/api/pause", "/api/resume"):
                return self.send_body(b"not found", "text/plain", 404)
            if not allowed(self.headers, port):
                return self.send_body(b"forbidden", "text/plain", 403)
            result = monitor.pause() if path == "/api/pause" else monitor.resume()
            self.send_body(json.dumps(result).encode("utf-8"), "application/json", 200 if result["ok"] else 500)

        def send_event(self, name: str, data: bytes) -> None:
            self.wfile.write(b"event: " + name.encode() + b"\ndata: " + data + b"\n\n")

        def stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            path = monitor.events
            lines, offset = tail_events(path)
            seen = -1
            try:
                version, state = monitor.state()
                self.send_event("state", json.dumps(state, default=str).encode("utf-8"))
                seen = version
                self.send_event("backlog", json.dumps([json.loads(line) for line in lines]).encode("utf-8"))
                self.wfile.flush()
                quiet = 0.0
                while True:
                    time.sleep(0.25)
                    sent = False
                    size = path.stat().st_size if path.exists() else 0
                    if size < offset:  # the log was replaced
                        offset = 0
                    if size > offset:
                        with path.open("rb") as handle:
                            handle.seek(offset)
                            chunk = handle.read(min(size - offset, 2_000_000))
                        complete = chunk.rfind(b"\n") + 1
                        offset += complete
                        for line in chunk[:complete].splitlines():
                            if line.strip():
                                self.send_event("event", line)
                                sent = True
                    version, state = monitor.state()
                    if version != seen:
                        seen = version
                        self.send_event("state", json.dumps(state, default=str).encode("utf-8"))
                        sent = True
                    quiet = 0.0 if sent else quiet + 0.25
                    if quiet >= 15:
                        self.wfile.write(b": keep-alive\n\n")
                        quiet = 0.0
                    if sent or quiet == 0.0:
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                return

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}/"
    monitor = Monitor()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(monitor, args.port))
    except OSError:  # already running (the logon task): just open it if asked
        if args.open:
            webbrowser.open(url)
        return 0
    server.daemon_threads = True
    stop = threading.Event()
    monitor.refresh_tasks()
    monitor.sample()
    threading.Thread(target=monitor.loop, args=(stop,), daemon=True, name="monitor-sample").start()
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
