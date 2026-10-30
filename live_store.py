"""SQLite store for the live site: stories, the URLs already seen, source schedules, the lead history and state.

One file under `.runtime/live_site/`; every write goes through one lock so the wire, desk and publisher threads
can share it. Nothing here calls a model or the network.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent
DEFAULT_PATH = ROOT / ".runtime" / "live_site" / "live.db"

# Story statuses, in the order a story moves through them.
QUEUED = "queued"          # found by the wire, not yet read
FIRST_PASS = "first_pass"  # on the page: summary written and checked against the article by code
VERIFIED = "verified"      # second model agreed with every sentence
HEADLINE = "headline"      # on the page as headline, source and link only
DROPPED = "dropped"        # off the page, with the reason in `error`
QUARANTINED = "quarantined"  # crashed a worker twice; left for the nightly review
VISIBLE = (FIRST_PASS, VERIFIED, HEADLINE)

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT '',
    section TEXT NOT NULL DEFAULT '',
    desk TEXT NOT NULL DEFAULT '',
    published TEXT NOT NULL DEFAULT '',
    found_at TEXT NOT NULL,
    shown_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    stage_attempts INTEGER NOT NULL DEFAULT 0,
    crashes INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    summary_method TEXT NOT NULL DEFAULT '',
    score INTEGER,
    score_parts TEXT NOT NULL DEFAULT '{}',
    official INTEGER NOT NULL DEFAULT 0,
    title_key TEXT NOT NULL DEFAULT '',
    group_id TEXT NOT NULL DEFAULT '',
    also TEXT NOT NULL DEFAULT '[]',
    page_text TEXT NOT NULL DEFAULT '',
    record TEXT NOT NULL DEFAULT '{}',
    notes TEXT NOT NULL DEFAULT '[]',
    error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS stories_status ON stories(status, found_at);
CREATE INDEX IF NOT EXISTS stories_title_key ON stories(title_key);
CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, first_seen TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
    name TEXT PRIMARY KEY,
    next_due TEXT NOT NULL,
    failures INTEGER NOT NULL DEFAULT 0,
    rest_until TEXT NOT NULL DEFAULT '',
    polls INTEGER NOT NULL DEFAULT 0,
    found INTEGER NOT NULL DEFAULT 0,
    shown INTEGER NOT NULL DEFAULT 0,
    etag TEXT NOT NULL DEFAULT '',
    modified TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    last_poll TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS leads (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id TEXT NOT NULL,
    set_at TEXT NOT NULL,
    score INTEGER NOT NULL,
    bar REAL,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "with", "at", "by", "from", "as", "is", "its", "after"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def url_key(url: str) -> str:
    """Scheme-, www-, query- and slash-insensitive key, so the same page found twice is one story."""
    url = re.sub(r"^https?://(www\.)?", "", url.strip().casefold())
    url = url.split("#", 1)[0].split("?", 1)[0]
    return url.rstrip("/")


def title_key(title: str) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", title.casefold()) if w not in _STOP]
    return " ".join(words)


def title_overlap(first: str, second: str) -> float:
    a, b = set(title_key(first).split()), set(title_key(second).split())
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


class Store:
    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            if str(path) != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
            self._db.commit()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._db
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- wire -------------------------------------------------------------------------------------------------
    def mark_seen(self, url: str, now: datetime) -> bool:
        """True the first time a URL is seen; False for every later sighting."""
        with self.tx() as db:
            cursor = db.execute("INSERT OR IGNORE INTO seen(key, first_seen) VALUES (?, ?)", (url_key(url), iso(now)))
            return cursor.rowcount == 1

    def add_story(self, record: dict[str, Any], now: datetime) -> str:
        story_id = str(record.get("id") or url_key(str(record["url"])))
        published = parse_iso(str(record.get("published", "")))
        if published is not None and published > now:
            published = now  # a bare date for today is stamped at noon; never date a story after we found it
        with self.tx() as db:
            db.execute(
                """INSERT OR IGNORE INTO stories(id, url, title, source, origin, published, found_at, status, title_key,
                   record, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (story_id, str(record["url"]), str(record.get("title", "")), str(record.get("source", "")),
                 str(record.get("origin", "")), iso(published) if published else "", iso(now), QUEUED,
                 title_key(str(record.get("title", ""))), json.dumps(record, ensure_ascii=False, default=str), iso(now)),
            )
        return story_id

    # --- stories ----------------------------------------------------------------------------------------------
    def get(self, story_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM stories WHERE id = ?", (story_id,)).fetchone()
        return _story(row) if row else None

    def update(self, story_id: str, now: datetime, **fields: Any) -> None:
        if not fields:
            return
        encoded = {k: json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v for k, v in fields.items()}
        encoded["updated_at"] = iso(now)
        columns = ", ".join(f"{k} = ?" for k in encoded)
        with self.tx() as db:
            db.execute(f"UPDATE stories SET {columns} WHERE id = ?", (*encoded.values(), story_id))

    def add_note(self, story_id: str, now: datetime, note: str) -> None:
        story = self.get(story_id)
        if story is None:
            return
        notes = story["notes"] + [{"t": iso(now), "note": note[:400]}]
        self.update(story_id, now, notes=notes[-20:])

    def by_status(self, *statuses: str, limit: int = 200, newest_first: bool = True) -> list[dict[str, Any]]:
        """Stories in these statuses; newest publication first, so fresh news is never stuck behind a backlog."""
        marks = ", ".join("?" for _ in statuses)
        order = "DESC" if newest_first else "ASC"
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM stories WHERE status IN ({marks}) ORDER BY published {order}, found_at {order} LIMIT ?",
                (*statuses, limit),
            ).fetchall()
        return [_story(r) for r in rows]

    def visible(self, since: datetime, limit: int = 400) -> list[dict[str, Any]]:
        """Stories on the page published since `since`, newest publication first (all times are stored in UTC)."""
        marks = ", ".join("?" for _ in VISIBLE)
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM stories WHERE status IN ({marks}) AND published >= ? ORDER BY published DESC LIMIT ?",
                (*VISIBLE, iso(since), limit),
            ).fetchall()
        return [_story(r) for r in rows]

    def similar_visible(self, title: str, since: datetime, threshold: float = 0.8) -> dict[str, Any] | None:
        """A story already on the page whose headline shares most words with `title` (same report, other outlet)."""
        for story in self.visible(since, limit=600):
            if title_overlap(title, story["title"]) >= threshold:
                return story
        return None

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT status, COUNT(*) AS n FROM stories GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    # --- sources ----------------------------------------------------------------------------------------------
    def source(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM sources WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def ensure_source(self, name: str, first_due: datetime) -> None:
        with self.tx() as db:
            db.execute("INSERT OR IGNORE INTO sources(name, next_due) VALUES (?, ?)", (name, iso(first_due)))

    def update_source(self, name: str, **fields: Any) -> None:
        columns = ", ".join(f"{k} = ?" for k in fields)
        with self.tx() as db:
            db.execute(f"UPDATE sources SET {columns} WHERE name = ?", (*fields.values(), name))

    def sources(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM sources ORDER BY name").fetchall()]

    # --- lead -------------------------------------------------------------------------------------------------
    def current_lead(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM leads ORDER BY seq DESC LIMIT 1").fetchone()
        if not row:
            return None
        lead = dict(row)
        lead["set_at"] = parse_iso(lead["set_at"])
        return lead

    def set_lead(self, story_id: str, now: datetime, score: int, bar: float | None, reason: str) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT INTO leads(story_id, set_at, score, bar, reason) VALUES (?, ?, ?, ?, ?)",
                (story_id, iso(now), int(score), bar, reason[:400]),
            )

    def lead_history(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM leads ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()]

    # --- state ------------------------------------------------------------------------------------------------
    def get_state(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_state(self, key: str, value: Any) -> None:
        with self.tx() as db:
            db.execute("INSERT OR REPLACE INTO state(key, value) VALUES (?, ?)", (key, json.dumps(value, default=str)))

    # --- housekeeping -----------------------------------------------------------------------------------------
    def prune(self, now: datetime, keep: timedelta = timedelta(days=14)) -> int:
        """Forget page text and seen URLs older than `keep`; stories themselves stay for the archive."""
        cutoff = iso(now - keep)
        with self.tx() as db:
            db.execute("UPDATE stories SET page_text = '' WHERE found_at < ? AND page_text != ''", (cutoff,))
            return db.execute("DELETE FROM seen WHERE first_seen < ?", (cutoff,)).rowcount


def _story(row: sqlite3.Row) -> dict[str, Any]:
    story = dict(row)
    for key in ("score_parts", "also", "record", "notes"):
        try:
            story[key] = json.loads(story[key] or ("{}" if key in ("score_parts", "record") else "[]"))
        except ValueError:
            story[key] = {} if key in ("score_parts", "record") else []
    return story
