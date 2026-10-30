"""The live page's market strip: the edition's Finnhub watchlist, refreshed every few minutes while NYSE is open.

Same endpoint, key and watchlist as market_watch.py (finnhub.io, free plan: 15 quotes a refresh, far under its 60
a minute). Outside trading hours it fetches once after each close and then rests. A failed fetch keeps the last
snapshot, the edition's data/market.json stands in when there is none, and the page hides the strip when neither
is fresh (market_watch.load_snapshot's five-day rule).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import market_watch
import newsroom_live
from calendar_rules import EASTERN, is_nyse_session, last_completed_nyse_session

OPEN, CLOSE = time(9, 30), time(16, 0)
SETTLE = time(16, 30)  # quotes after the close settle by then (calendar_rules uses the same margin)


def market_open(now: datetime) -> bool:
    local = now.astimezone(EASTERN)
    return is_nyse_session(local.date()) and OPEN <= local.time() < CLOSE


def last_close(now: datetime) -> datetime:
    """When the most recent completed session's closing quotes were final."""
    day = last_completed_nyse_session(now)
    return datetime.combine(day, SETTLE, EASTERN).astimezone(timezone.utc)


class MarketWatch:
    def __init__(
        self,
        config: dict[str, Any],
        path: Path,
        *,
        fetch: Callable[[], dict[str, Any] | None] = market_watch.fetch_snapshot,
        fallback: Path = market_watch.SNAPSHOT,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.every = timedelta(minutes=float(config["market"]["every_minutes"]))
        self.path, self.fetch, self.fallback, self.clock = path, fetch, fallback, clock
        self.last_attempt: datetime | None = None

    def _as_of(self) -> datetime | None:
        try:
            return datetime.fromisoformat(json.loads(self.path.read_text(encoding="utf-8"))["as_of"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def due(self, now: datetime) -> bool:
        if self.last_attempt and now - self.last_attempt < self.every:
            return False  # also spaces out retries after a failure
        as_of = self._as_of()
        if as_of is None:
            return True
        if market_open(now):
            return now - as_of >= self.every
        return as_of < last_close(now) <= now  # once after each close, then rest until the next open

    def refresh(self) -> bool:
        """Fetch a new snapshot if one is due; True when a new one was saved. Never raises."""
        now = self.clock()
        if not self.due(now):
            return False
        self.last_attempt = now
        try:
            snapshot = self.fetch()
        except Exception as exc:  # noqa: BLE001 - the strip is a nicety; a failure keeps the last snapshot
            newsroom_live.emit("live-market-fail", desk="wire", error=repr(exc)[:200])
            return False
        if not snapshot:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, self.path)
        newsroom_live.emit("live-market", desk="wire", quotes=len(snapshot.get("quotes", [])))
        return True

    def current(self) -> dict[str, Any] | None:
        return market_watch.load_snapshot(self.path) or market_watch.load_snapshot(self.fallback)

    def run(self, stop: threading.Event, beat: Callable[[], None] = lambda: None) -> None:
        while not stop.is_set():
            beat()
            self.refresh()
            stop.wait(30)


def public_market(snapshot: dict[str, Any] | None, now: datetime) -> dict[str, Any] | None:
    """What the page shows: a label from the quotes' own timestamps, and each quote's price and change."""
    if not snapshot or not snapshot.get("session_label"):
        return None  # no usable quote timestamps: hide the strip rather than guess a date
    return {
        "label": snapshot["session_label"],
        "as_of": snapshot.get("as_of", ""),
        "open": market_open(now),
        "quotes": [
            {k: q[k] for k in ("symbol", "name", "group", "price", "change", "percent") if k in q}
            for q in snapshot["quotes"]
        ],
    }
