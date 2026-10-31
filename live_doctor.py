"""The live site's doctor: service levels, model fallback, heartbeats and worker restarts.

Levels step down instead of stopping and climb back by themselves:
  LIVE       every lane running
  LITE       the checker is unhealthy, its queue is too old, or the edition has the GPU; first pass keeps flowing
  HEADLINES  the writer (and its fallback) is down; new stories show as headline, source and link
  FROZEN     the feed cannot be written; the page keeps the last good feed and says when it paused
A crashed worker thread is restarted here; a hung one stops the heartbeat file, and the services check restarts
the whole process from outside.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import newsroom_live
from live_desk import Desk
from live_store import FIRST_PASS, Store, iso, parse_iso

LEVELS = ("LIVE", "LITE", "HEADLINES", "FROZEN")
FALLBACK_RETRY = timedelta(minutes=15)


def compute_level(
    *,
    writer_failures: int,
    verifier_failures: int,
    oldest_unverified: datetime | None,
    gpu_busy: bool,
    feed_ok: bool,
    now: datetime,
    config: dict[str, Any],
) -> tuple[str, str]:
    """(level, reason) from the current health signals. Pure, so every rule has a test."""
    doctor = config["doctor"]
    if not feed_ok:
        return "FROZEN", "the feed could not be written"
    if writer_failures >= 3:
        return "HEADLINES", f"the writer failed {writer_failures} times in a row"
    if gpu_busy:
        return "LITE", "the GPU is held: the morning edition, or the owner paused model work"
    if verifier_failures >= 3:
        return "LITE", f"the checker failed {verifier_failures} times in a row"
    if oldest_unverified and now - oldest_unverified > timedelta(minutes=float(doctor["lite_queue_minutes"])):
        minutes = int((now - oldest_unverified).total_seconds() // 60)
        return "LITE", f"the oldest unchecked story has waited {minutes} min"
    return "LIVE", ""


@dataclass
class Worker:
    name: str
    target: Callable[[threading.Event, Callable[[], None]], None]
    thread: threading.Thread | None = None
    beat_at: float = 0.0
    restarts: int = 0


class Doctor:
    def __init__(
        self,
        store: Store,
        desk: Desk,
        config: dict[str, Any],
        *,
        primary_writer: Any,
        fallback_writer: Any | None,
        heartbeat_path: Path,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.store = store
        self.desk = desk
        self.config = config
        self.primary = primary_writer
        self.fallback = fallback_writer
        self.heartbeat_path = heartbeat_path
        self.clock = clock
        self.workers: dict[str, Worker] = {}
        self.stop = threading.Event()
        self.feed_ok = True
        self.level = "LIVE"
        self.reason = ""
        self.on_fallback_since: datetime | None = None

    # --- workers ----------------------------------------------------------------------------------------------
    def add_worker(self, name: str, target: Callable[[threading.Event, Callable[[], None]], None]) -> None:
        self.workers[name] = Worker(name, target)

    def _start(self, worker: Worker) -> None:
        def beat() -> None:
            worker.beat_at = time.monotonic()

        def body() -> None:
            try:
                worker.target(self.stop, beat)
            except Exception as exc:  # noqa: BLE001 - logged; the supervisor restarts the worker
                newsroom_live.emit("live-worker-crash", desk="doctor", worker=worker.name, error=repr(exc)[:300])

        worker.beat_at = time.monotonic()
        worker.thread = threading.Thread(target=body, name=f"live-{worker.name}", daemon=True)
        worker.thread.start()

    def start(self) -> None:
        for worker in self.workers.values():
            self._start(worker)

    def supervise(self) -> list[str]:
        """Restart dead workers; return the names of workers that are alive but silent (hung)."""
        silent_after = float(self.config["doctor"]["silent_after_seconds"])
        hung = []
        for worker in self.workers.values():
            if worker.thread is None or not worker.thread.is_alive():
                if self.stop.is_set():
                    continue
                worker.restarts += 1
                newsroom_live.emit("live-worker-restart", desk="doctor", worker=worker.name, restarts=worker.restarts)
                self._start(worker)
            elif time.monotonic() - worker.beat_at > silent_after:
                hung.append(worker.name)
        return hung

    # --- models -----------------------------------------------------------------------------------------------
    def manage_writer(self, now: datetime) -> None:
        """Two writer failures in a row: switch to the fallback model. Try the primary again every 15 minutes."""
        failures = self.desk.health.failures.get("writer", 0)
        restarts = int(self.config["doctor"]["writer_restarts"])
        if self.desk.writer is self.primary and self.fallback is not None and failures >= restarts:
            self._use_writer(self.fallback, "fallback")
            self.on_fallback_since = now
        elif self.desk.writer is self.fallback and self.on_fallback_since and now - self.on_fallback_since >= FALLBACK_RETRY:
            self._use_writer(self.primary, "primary")
            self.on_fallback_since = None

    def _use_writer(self, provider: Any, which: str) -> None:
        import grounding

        self.desk.writer = provider
        self.desk.write = grounding.make_writer(provider)
        self.desk.health.failures["writer"] = 0
        newsroom_live.emit("live-writer", desk="doctor", which=which, model=getattr(provider, "model", ""))

    # --- one tick ---------------------------------------------------------------------------------------------
    def tick(self) -> str:
        now = self.clock()
        hung = self.supervise()
        self.manage_writer(now)
        waiting = self.store.by_status(FIRST_PASS, limit=1, newest_first=False)
        oldest = parse_iso(waiting[0]["shown_at"]) if waiting else None
        level, reason = compute_level(
            writer_failures=self.desk.health.failures.get("writer", 0),
            verifier_failures=self.desk.health.failures.get("verifier", 0),
            oldest_unverified=oldest,
            gpu_busy=self.desk.gpu_busy(),
            feed_ok=self.feed_ok,
            now=now,
            config=self.config,
        )
        if level != self.level:
            newsroom_live.emit("live-level", desk="doctor", level=level, previous=self.level, reason=reason)
            self.store.set_state("level_changed_at", iso(now))
        self.level, self.reason = level, reason
        self.desk.level = level
        self.store.set_state("level", {"level": level, "reason": reason, "at": iso(now)})
        if not hung:
            # Only a fully healthy process refreshes the heartbeat; a stale file tells the services check to restart us.
            self._write_heartbeat(now)
        else:
            newsroom_live.emit("live-hung", desk="doctor", workers=hung)
        return level

    def _write_heartbeat(self, now: datetime) -> None:
        try:
            self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "at": iso(now), "level": self.level, "reason": self.reason,
                "writer": getattr(self.desk.writer, "model", ""),
                "workers": {w.name: w.restarts for w in self.workers.values()},
                "counts": self.store.counts(),
                "unsorted": self.store.unsorted(now - timedelta(hours=float(self.config["site"]["river_hours"]))),
            }
            temp = self.heartbeat_path.with_suffix(".tmp")
            temp.write_text(json.dumps(data), encoding="utf-8")
            temp.replace(self.heartbeat_path)
        except OSError:
            pass
