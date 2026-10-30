"""Run the live site: wire, fast lane, slow lane, publisher and doctor in one supervised process.

    python live_service.py run [--port 8766]   the service (a local preview at http://127.0.0.1:8766/)
    python live_service.py sweep               poll every source once (one Google query) and print what was queued
    python live_service.py render              write feed.json from the store and exit

Events go to `.runtime/live_site/events.jsonl` (not the edition's dashboard feed), so the morning dashboard only
ever shows the edition.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".runtime" / "live_site"
os.environ.setdefault("NEWSROOM_LIVE_DIR", str(RUNTIME))

import live_feed  # noqa: E402
import newsroom_live  # noqa: E402
from ai_providers import DEFAULT_OLLAMA_BASE_URL, OllamaProvider  # noqa: E402
from live_desk import Desk  # noqa: E402
from live_doctor import Doctor  # noqa: E402
from live_market import MarketWatch, public_market  # noqa: E402
from live_sources import Wire, load_config  # noqa: E402
from live_store import Store, iso  # noqa: E402


def providers(config: dict[str, Any]) -> dict[str, OllamaProvider]:
    desk = config["desk"]
    base = os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL)
    stall = int(config["doctor"]["model_stall_seconds"])
    budgets = desk["budgets_seconds"]
    common = {"base_url": base, "stall_timeout_seconds": stall, "num_ctx": 8192, "max_attempts": 2}
    return {
        # The writer stays resident: the fast lane needs it within seconds, all day.
        "writer": OllamaProvider(model=desk["writer_model"], timeout_seconds=int(budgets["first_pass"]), keep_alive="24h", **common),
        "fallback": OllamaProvider(model=desk["fallback_model"], timeout_seconds=int(budgets["first_pass"]), **common),
        # The checker is another model family; it loads for a batch and leaves (Ollama's default keep-alive).
        "verifier": OllamaProvider(model=desk["verifier_model"], timeout_seconds=int(budgets["verify"]), **common),
        # Scoring is the one fast-lane-adjacent call that thinks; it runs in the slow lane only.
        "scorer": OllamaProvider(model=desk["writer_model"], timeout_seconds=int(budgets["score"]), think=True, keep_alive="24h", **common),
    }


def single_instance(path: Path):
    """Hold an exclusive lock for the life of the process; None if another live service already holds it."""
    import msvcrt

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def wire_name(name: str) -> str:
    """'Trade: DroneDJ' -> 'DroneDJ'; a Google query keeps its engine: 'Google News: “eVTOL”'."""
    kind, _, rest = name.partition(": ")
    if kind == "Google News":
        return f"Google News: “{rest.strip(chr(34))}”"
    return rest or name


def wire_status(store: Store) -> list[dict[str, Any]]:
    rows = sorted((s for s in store.sources() if s["last_poll"]), key=lambda s: s["last_poll"], reverse=True)
    return [{"source": wire_name(s["name"]), "at": s["last_poll"], "ok": not s["last_error"]} for s in rows[:8]]


def market_watch_for(config: dict[str, Any]) -> MarketWatch:
    return MarketWatch(config, ROOT / config["market"]["output"])


class Publisher:
    def __init__(self, store: Store, config: dict[str, Any], doctor: Doctor, out_dir: Path, market: MarketWatch) -> None:
        self.store, self.config, self.doctor, self.out_dir, self.market = store, config, doctor, out_dir, market
        self.last_hash = ""

    def publish(self) -> bool:
        now = datetime.now(timezone.utc)
        try:
            feed = live_feed.build_feed(
                self.store, self.config, now=now, level=self.doctor.level, wire=wire_status(self.store),
                market=public_market(self.market.current(), now),
            )
            live_feed.write_site(self.out_dir, feed)
        except Exception as exc:  # noqa: BLE001 - the doctor turns this into FROZEN
            self.doctor.feed_ok = False
            newsroom_live.emit("live-publish-fail", desk="press", error=repr(exc)[:300])
            return False
        self.doctor.feed_ok = True
        digest = live_feed.content_hash(feed)
        if digest != self.last_hash:
            self.last_hash = digest
            newsroom_live.emit("live-publish", desk="press", stories=len(feed["stories"]), lead=(feed["lead"] or {}).get("title", "")[:120])
        return True

    def run(self, stop: threading.Event, beat) -> None:
        every = float(self.config["publish"]["every_seconds"])
        while not stop.is_set():
            beat()
            self.publish()
            stop.wait(every)


def serve_preview(out_dir: Path, port: int) -> http.server.ThreadingHTTPServer:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(Quiet, directory=str(out_dir)))
    threading.Thread(target=server.serve_forever, name="live-preview", daemon=True).start()
    return server


def build(config: dict[str, Any], store: Store) -> tuple[Wire, Desk, Doctor, Publisher, MarketWatch, Path]:
    models = providers(config)
    desk = Desk(store, config, writer=models["writer"], verifier=models["verifier"], scorer=models["scorer"])
    doctor = Doctor(store, desk, config, primary_writer=models["writer"], fallback_writer=models["fallback"], heartbeat_path=RUNTIME / "heartbeat.json")
    out_dir = ROOT / config["publish"]["output"]
    market = market_watch_for(config)
    publisher = Publisher(store, config, doctor, out_dir, market)
    return Wire(store, config), desk, doctor, publisher, market, out_dir


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("run", "sweep", "render"))
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args(argv)
    config = load_config()
    store = Store()

    if args.command == "render":
        out_dir = ROOT / config["publish"]["output"]
        level = (store.get_state("level") or {}).get("level", "LIVE")
        now = datetime.now(timezone.utc)
        market = public_market(market_watch_for(config).current(), now)
        live_feed.write_site(out_dir, live_feed.build_feed(store, config, now=now, level=level, wire=wire_status(store), market=market))
        print(out_dir / "feed.json")
        return 0
    if args.command == "sweep":
        wire = Wire(store, config)
        now = datetime.now(timezone.utc)
        for source in wire.sources:  # a manual sweep checks every source now (Google: one query, as always)
            store.update_source(source.name, next_due=iso(now))
        print(f"queued {wire.run_once()} new stories; {store.counts()}")
        return 0

    lock = single_instance(RUNTIME / "service.lock")
    if lock is None:
        print("The live service is already running.", file=sys.stderr)
        return 0
    wire, desk, doctor, publisher, market, out_dir = build(config, store)
    doctor.add_worker("wire", wire.run)
    doctor.add_worker("market", market.run)
    doctor.add_worker("fast", desk.run_fast)
    doctor.add_worker("slow", desk.run_slow)
    doctor.add_worker("publish", publisher.run)
    publisher.publish()
    serve_preview(out_dir, args.port)
    newsroom_live.emit("live-start", desk="doctor", port=args.port, pid=os.getpid())
    doctor.start()
    every = float(config["doctor"]["heartbeat_seconds"])
    try:
        while not doctor.stop.is_set():
            doctor.tick()
            doctor.stop.wait(every)
    except KeyboardInterrupt:
        doctor.stop.set()
    newsroom_live.emit("live-stop", desk="doctor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
