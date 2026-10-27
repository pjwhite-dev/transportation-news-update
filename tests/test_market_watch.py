from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import market_watch
import public_site


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, quotes, fail=()):
        self.quotes, self.fail, self.calls = quotes, set(fail), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers))
        symbol = params["symbol"]
        if symbol in self.fail:
            raise market_watch.requests.ConnectionError("down")
        return FakeResponse(self.quotes.get(symbol, {"c": 0, "pc": 0, "t": 0}))


GOOD = {"c": 110.0, "pc": 100.0, "t": 1790000000}
NOW = datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc)


def all_quotes():
    return {symbol: dict(GOOD) for symbol, _, _ in market_watch.WATCHLIST}


class FetchTests(unittest.TestCase):
    def test_snapshot_computes_change_and_keeps_key_out_of_url(self):
        session = FakeSession(all_quotes())
        snap = market_watch.fetch_snapshot(session=session, secrets=lambda name: "k", now=NOW)
        self.assertEqual(len(snap["quotes"]), len(market_watch.WATCHLIST))
        first = snap["quotes"][0]
        self.assertEqual((first["change"], first["percent"]), (10.0, 10.0))
        for url, params, headers in session.calls:
            self.assertNotIn("token", params)
            self.assertEqual(headers["X-Finnhub-Token"], "k")

    def test_missing_key_or_too_few_quotes_returns_none(self):
        self.assertIsNone(market_watch.fetch_snapshot(session=FakeSession(all_quotes()), secrets=lambda n: None))
        few = FakeSession(all_quotes(), fail=[s for s, _, _ in market_watch.WATCHLIST][:len(market_watch.WATCHLIST) - market_watch.MINIMUM_QUOTES + 1])
        self.assertIsNone(market_watch.fetch_snapshot(session=few, secrets=lambda n: "k"))

    def test_one_failed_symbol_is_skipped(self):
        session = FakeSession(all_quotes(), fail=["BA"])
        snap = market_watch.fetch_snapshot(session=session, secrets=lambda n: "k", now=NOW)
        self.assertNotIn("BA", [q["symbol"] for q in snap["quotes"]])

    def test_bad_quote_values_are_dropped(self):
        quotes = all_quotes()
        quotes["BA"] = {"c": 0, "pc": 100, "t": 1}
        snap = market_watch.fetch_snapshot(session=FakeSession(quotes), secrets=lambda n: "k", now=NOW)
        self.assertNotIn("BA", [q["symbol"] for q in snap["quotes"]])


class LoadTests(unittest.TestCase):
    def snapshot(self, as_of="2026-10-27T08:00:00+00:00", count=len(market_watch.WATCHLIST)):
        quotes = [
            {"symbol": s, "name": n, "group": g, "price": 10.0, "change": 1.0, "percent": 1.0 if i % 2 else -1.0}
            for i, (s, n, g) in enumerate(market_watch.WATCHLIST[:count])
        ]
        return {"as_of": as_of, "source": "Finnhub", "quotes": quotes}

    def write(self, data):
        directory = tempfile.mkdtemp()
        path = Path(directory) / "market.json"
        path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
        return path

    def test_fresh_snapshot_loads(self):
        path = self.write(self.snapshot())
        self.assertEqual(len(market_watch.load_snapshot(path, today=date(2026, 10, 28))["quotes"]), len(market_watch.WATCHLIST))

    def test_stale_missing_malformed_or_thin_snapshots_are_hidden(self):
        self.assertIsNone(market_watch.load_snapshot(self.write(self.snapshot()), today=date(2026, 11, 20)))
        self.assertIsNone(market_watch.load_snapshot(Path("does-not-exist.json"), today=date(2026, 10, 28)))
        self.assertIsNone(market_watch.load_snapshot(self.write("{not json"), today=date(2026, 10, 28)))
        self.assertIsNone(market_watch.load_snapshot(self.write(self.snapshot(count=3)), today=date(2026, 10, 28)))


class RenderTests(unittest.TestCase):
    def snapshot(self):
        return LoadTests().snapshot()

    def test_markup_has_names_groups_and_text_direction(self):
        markup = public_site.market_html(self.snapshot())
        self.assertIn('id="market"', markup)
        self.assertIn('title="Boeing"', markup)
        self.assertIn("eVTOL", markup)
        self.assertIn("Joby Aviation", markup)
        self.assertIn("▲", markup)
        self.assertIn("▼", markup)
        self.assertIn("not investment advice", markup)

    def test_no_snapshot_renders_nothing(self):
        self.assertEqual(public_site.market_html(None), "")

    def test_names_are_escaped(self):
        snap = self.snapshot()
        snap["quotes"][0]["name"] = '<b>"x"</b>'
        self.assertNotIn("<b>", public_site.market_html(snap))


if __name__ == "__main__":
    unittest.main()
