"""Market watch: a small, fail-soft snapshot of transportation and aerospace tickers.

    python market_watch.py            # fetch quotes and write data/market.json

The only endpoint contacted is finnhub.io (free plan), with a key read from the Windows
Credential Manager. Any failure leaves the previous snapshot alone, and the site hides the
box when the snapshot is missing or stale, so this can never block an edition.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from alerts import get_secret
from calendar_rules import EASTERN, last_completed_nyse_session, session_label

QUOTE_URL = "https://finnhub.io/api/v1/quote"
SECRET = "finnhub_api_key"
SNAPSHOT = Path(__file__).resolve().parent / "data" / "market.json"
MAX_AGE_DAYS = 5
MINIMUM_QUOTES = 6

# (symbol, business name, group). Order is display order within each group.
# The free plan has no index levels, so the overview uses the ETFs that track each index.
WATCHLIST: tuple[tuple[str, str, str], ...] = (
    ("SPY", "S&P 500 (SPDR S&P 500 ETF)", "Market overview"),
    ("DIA", "Dow Jones Industrial Average (SPDR Dow Jones ETF)", "Market overview"),
    ("QQQ", "Nasdaq-100 (Invesco QQQ ETF)", "Market overview"),
    ("BA", "Boeing", "Aerospace & defense"),
    ("LMT", "Lockheed Martin", "Aerospace & defense"),
    ("RTX", "RTX Corporation", "Aerospace & defense"),
    ("GE", "GE Aerospace", "Aerospace & defense"),
    ("UNP", "Union Pacific", "Rail & freight"),
    ("CSX", "CSX Corporation", "Rail & freight"),
    ("UPS", "United Parcel Service", "Rail & freight"),
    ("FDX", "FedEx", "Rail & freight"),
    ("DAL", "Delta Air Lines", "Airlines"),
    ("UAL", "United Airlines Holdings", "Airlines"),
    ("JOBY", "Joby Aviation", "eVTOL"),
    ("ACHR", "Archer Aviation", "eVTOL"),
)


def _quote(session: requests.Session, symbol: str, key: str) -> dict[str, Any] | None:
    response = session.get(QUOTE_URL, params={"symbol": symbol}, headers={"X-Finnhub-Token": key}, timeout=15)
    response.raise_for_status()
    data = response.json()
    price, previous, stamp = data.get("c"), data.get("pc"), data.get("t")
    if not isinstance(price, (int, float)) or not isinstance(previous, (int, float)) or price <= 0 or previous <= 0:
        return None
    change = round(price - previous, 2)
    return {
        "price": round(float(price), 2),
        "change": change,
        "percent": round(change / previous * 100, 2),
        "quote_time": datetime.fromtimestamp(stamp, timezone.utc).isoformat() if isinstance(stamp, (int, float)) and stamp > 0 else "",
    }


def fetch_snapshot(
    *,
    session: requests.Session | None = None,
    secrets: Callable[[str], str | None] = get_secret,
    now: datetime | None = None,
    watchlist: tuple[tuple[str, str, str], ...] = WATCHLIST,
) -> dict[str, Any] | None:
    """Return a new snapshot, or None when the key is missing or too few quotes came back."""
    key = secrets(SECRET)
    if not key:
        print("market_watch: no finnhub_api_key stored; skipping.", file=sys.stderr)
        return None
    session = session or requests.Session()
    quotes = []
    for symbol, name, group in watchlist:
        try:
            result = _quote(session, symbol, key)
        except (requests.RequestException, ValueError) as exc:
            print(f"market_watch: {symbol} failed: {type(exc).__name__}", file=sys.stderr)
            continue
        if result:
            quotes.append({"symbol": symbol, "name": name, "group": group, **result})
    if len(quotes) < MINIMUM_QUOTES:
        print(f"market_watch: only {len(quotes)} quotes; keeping the previous snapshot.", file=sys.stderr)
        return None
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {"as_of": stamp.isoformat(), "source": "Finnhub", "quotes": quotes}


def snapshot_label(snapshot: dict[str, Any], now: datetime | None = None) -> str:
    """'Close, Mon., Oct. 26' for the last completed NYSE session; the data's real date when it differs.

    The label comes from the quotes' own timestamps (America/New_York), never from the clock alone. A
    snapshot with no usable timestamps has no label, and the site hides the panel.
    """
    stamps = []
    for quote in snapshot.get("quotes", []):
        try:
            stamps.append(datetime.fromisoformat(str(quote["quote_time"])).astimezone(EASTERN))
        except (KeyError, ValueError, TypeError):
            continue
    if not stamps:
        return ""
    day = Counter(stamp.date() for stamp in stamps).most_common(1)[0][0]
    on_day = [stamp for stamp in stamps if stamp.date() == day]
    intraday = sum(time(9, 30) <= stamp.time() < time(16, 0) for stamp in on_day) > len(on_day) / 2
    expected = last_completed_nyse_session(now or datetime.now(EASTERN))
    if day == expected and not intraday:
        return session_label(day)
    return session_label(day, "Intraday" if intraday else "Close")


def load_snapshot(path: Path = SNAPSHOT, *, today: date | None = None) -> dict[str, Any] | None:
    """The saved snapshot, or None when it is missing, malformed or older than MAX_AGE_DAYS."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        as_of = datetime.fromisoformat(data["as_of"]).date()
        quotes = [
            q for q in data["quotes"]
            if isinstance(q.get("price"), (int, float)) and isinstance(q.get("percent"), (int, float)) and q.get("symbol")
        ]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if (today or date.today()).toordinal() - as_of.toordinal() > MAX_AGE_DAYS or len(quotes) < MINIMUM_QUOTES:
        return None
    snapshot = {**data, "quotes": quotes}
    snapshot["session_label"] = snapshot_label(snapshot)
    return snapshot


def main() -> int:
    snapshot = fetch_snapshot()
    if snapshot is None:
        return 0  # fail-soft: never block an edition
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    print(f"market_watch: saved {len(snapshot['quotes'])} quotes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
