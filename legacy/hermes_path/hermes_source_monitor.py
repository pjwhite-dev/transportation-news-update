from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

import requests


ROOT = (
    Path.cwd()
    if (Path.cwd() / "news_engine.py").is_file()
    else Path(__file__).resolve().parents[1]
)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from news_engine import (  # noqa: E402
    EASTERN,
    FEDERAL_REGISTER_TERMS,
    NEWS_QUERIES,
    automated_record_is_portfolio_relevant,
    deduplicate_articles,
    fetch_google_news,
)
from web_discovery import (  # noqa: E402
    HEADERS,
    OFFICIAL_FEEDS,
    clean_spaces,
    fetch_official_feeds,
    normalize_url,
    stable_id,
)


RUNTIME = ROOT / ".runtime" / "atu-monitor"
DB_PATH = RUNTIME / "monitor.sqlite3"
LOCK_PATH = RUNTIME / "cycle.lock"
QUEUE_PATH = RUNTIME / "queue.json"
HEALTH_PATH = RUNTIME / "health.json"
GOOGLE_BATCH_SIZE = 4
MAX_QUEUE_ITEMS = 300


def utc_timestamp() -> str:
    return datetime.now(EASTERN).isoformat()


def atomic_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


@contextmanager
def cycle_lock(stale_seconds: int = 600) -> Iterator[None]:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        age = time.time() - LOCK_PATH.stat().st_mtime
        if age < stale_seconds:
            raise RuntimeError(f"monitor cycle already running ({age:.0f}s old)")
        LOCK_PATH.unlink(missing_ok=True)
        descriptor = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(
            descriptor,
            json.dumps({"pid": os.getpid(), "started_at": utc_timestamp()}).encode(),
        )
        os.close(descriptor)
        yield
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        LOCK_PATH.unlink(missing_ok=True)


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS candidates (
            identity TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            source TEXT NOT NULL,
            search_section TEXT NOT NULL,
            published TEXT NOT NULL,
            origin TEXT NOT NULL,
            summary TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            priority INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            payload_json TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS candidates_url_idx
            ON candidates(url) WHERE url <> '';
        CREATE TABLE IF NOT EXISTS source_health (
            source_key TEXT PRIMARY KEY,
            last_attempt TEXT NOT NULL,
            last_success TEXT,
            consecutive_errors INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            items_seen INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    return connection


def get_state(connection: sqlite3.Connection, key: str, default: str = "") -> str:
    row = connection.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def set_state(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO state(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def mark_health(
    connection: sqlite3.Connection,
    source_key: str,
    *,
    success: bool,
    item_count: int = 0,
    error: str = "",
) -> None:
    now = utc_timestamp()
    connection.execute(
        """
        INSERT INTO source_health(
            source_key, last_attempt, last_success, consecutive_errors,
            last_error, items_seen
        ) VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_key) DO UPDATE SET
            last_attempt = excluded.last_attempt,
            last_success = CASE WHEN ? THEN excluded.last_success ELSE source_health.last_success END,
            consecutive_errors = CASE WHEN ? THEN 0 ELSE source_health.consecutive_errors + 1 END,
            last_error = excluded.last_error,
            items_seen = source_health.items_seen + excluded.items_seen
        """,
        (
            source_key,
            now,
            now if success else None,
            0 if success else 1,
            error[:1000],
            item_count,
            success,
            success,
        ),
    )


def priority(item: dict[str, Any]) -> int:
    text = " ".join(
        str(item.get(key, ""))
        for key in ("title", "summary", "source", "search_section")
    ).casefold()
    score = 30
    for needle, points in (
        ("federal register", 22),
        ("final rule", 20),
        ("proposed rule", 18),
        ("deadline", 16),
        ("faa", 14),
        ("nhtsa", 14),
        ("fmcsa", 14),
        ("tsa", 14),
        ("bvlos", 14),
        ("part 108", 14),
        ("counter-uas", 12),
        ("c-uas", 12),
        ("autonomous", 8),
        ("evtol", 8),
        ("supersonic", 8),
        ("high-speed rail", 8),
    ):
        if needle in text:
            score += points
    if str(item.get("origin", "")).casefold().startswith("official"):
        score += 18
    return min(score, 100)


def federal_register_term(
    term: str, window_start: datetime, window_end: datetime
) -> tuple[list[dict[str, Any]], str | None]:
    endpoint = "https://www.federalregister.gov/api/v1/documents.json"
    params = {
        "per_page": 20,
        "order": "newest",
        "conditions[term]": term,
        "conditions[publication_date][gte]": window_start.date().isoformat(),
        "conditions[publication_date][lte]": window_end.date().isoformat(),
    }
    try:
        response = requests.get(endpoint, params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        results = response.json().get("results", [])
    except (requests.RequestException, ValueError) as exc:
        return [], str(exc)

    items: list[dict[str, Any]] = []
    for result in results:
        publication_date = str(result.get("publication_date", ""))
        try:
            published = datetime.fromisoformat(publication_date).replace(
                hour=12, tzinfo=EASTERN
            )
        except ValueError:
            continue
        if not (window_start <= published <= window_end):
            continue
        title = clean_spaces(str(result.get("title", "Untitled federal action")))
        agencies = ", ".join(
            str(agency.get("name", ""))
            for agency in result.get("agencies", [])
            if agency.get("name")
        )
        url = normalize_url(str(result.get("html_url") or result.get("pdf_url") or ""))
        items.append(
            {
                "id": stable_id("Federal Register", url, title),
                "search_section": "Federal Register",
                "title": title,
                "summary": clean_spaces(str(result.get("abstract", "")))[:700],
                "source": agencies or "Federal Register",
                "url": url,
                "published": published.isoformat(),
                "date_label": published.strftime("%b. %d, %Y").replace(" 0", " "),
                "origin": "Federal Register API",
            }
        )
    return items, None


def upsert_candidates(
    connection: sqlite3.Connection, items: list[dict[str, Any]]
) -> tuple[int, int]:
    now = utc_timestamp()
    inserted = 0
    refreshed = 0
    for item in deduplicate_articles(items):
        if not automated_record_is_portfolio_relevant(item):
            continue
        url = str(item.get("url", ""))
        proposed_identity = str(
            item.get("id") or stable_id(url, str(item.get("title", "")))
        )
        payload = json.dumps(item, ensure_ascii=False, sort_keys=True)
        # A record can be refetched under a different generated id (e.g. a
        # changed headline) while keeping the same URL. Resolve the canonical
        # identity first: if a row already exists for this identity or this URL,
        # update that exact row. This keeps the upsert consistent with the
        # unique candidates.url index, which ON CONFLICT(identity) alone does
        # not cover.
        # Prefer the URL owner when identity and URL point at different legacy
        # rows. Updating the identity owner in that case would violate the
        # unique URL index before SQLite can apply the identity upsert.
        existing = None
        if url:
            existing = connection.execute(
                "SELECT identity FROM candidates WHERE url = ? LIMIT 1", (url,)
            ).fetchone()
        if existing is None:
            existing = connection.execute(
                "SELECT identity FROM candidates WHERE identity = ? LIMIT 1",
                (proposed_identity,),
            ).fetchone()
        identity = existing["identity"] if existing else proposed_identity
        existed = existing is not None
        connection.execute(
            """
            INSERT INTO candidates(
                identity, url, title, source, search_section, published, origin,
                summary, first_seen, last_seen, priority, payload_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(identity) DO UPDATE SET
                url = excluded.url,
                title = excluded.title,
                source = excluded.source,
                search_section = excluded.search_section,
                published = excluded.published,
                origin = excluded.origin,
                summary = excluded.summary,
                last_seen = excluded.last_seen,
                priority = MAX(candidates.priority, excluded.priority),
                payload_json = excluded.payload_json
            """,
            (
                identity,
                str(item.get("url", "")),
                str(item.get("title", "")),
                str(item.get("source", "")),
                str(item.get("search_section", "")),
                str(item.get("published", "")),
                str(item.get("origin", "")),
                str(item.get("summary", "")),
                now,
                now,
                priority(item),
                payload,
            ),
        )
        if existed:
            refreshed += 1
        else:
            inserted += 1
    return inserted, refreshed


def write_snapshots(connection: sqlite3.Connection, cycle: dict[str, Any]) -> None:
    rows = connection.execute(
        """
        SELECT payload_json, priority, first_seen, last_seen, status
        FROM candidates
        WHERE status = 'queued'
        ORDER BY priority DESC, published DESC, first_seen DESC
        LIMIT ?
        """,
        (MAX_QUEUE_ITEMS,),
    ).fetchall()
    queue = []
    for row in rows:
        item = json.loads(row["payload_json"])
        item.update(
            {
                "monitor_priority": row["priority"],
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
                "monitor_status": row["status"],
            }
        )
        queue.append(item)
    health = [dict(row) for row in connection.execute(
        "SELECT * FROM source_health ORDER BY source_key"
    )]
    atomic_json(QUEUE_PATH, {"generated_at": utc_timestamp(), "items": queue})
    atomic_json(HEALTH_PATH, {"last_cycle": cycle, "sources": health})


def due(last_value: str, minutes: int, now: datetime) -> bool:
    if not last_value:
        return True
    try:
        return now - datetime.fromisoformat(last_value) >= timedelta(minutes=minutes)
    except ValueError:
        return True


def run_cycle(*, dry_run: bool = False) -> dict[str, Any]:
    now = datetime.now(EASTERN)
    window_start = now - timedelta(hours=48)
    with cycle_lock(), connect() as connection:
        if dry_run:
            cycle = {
                "status": "dry-run",
                "finished_at": utc_timestamp(),
                "database": str(DB_PATH),
            }
            write_snapshots(connection, cycle)
            connection.commit()
            return cycle

        gathered: list[dict[str, Any]] = []
        errors: list[str] = []
        polled: list[str] = []

        query_pairs = [
            (section, query)
            for section, queries in NEWS_QUERIES.items()
            for query in queries
        ]
        cursor = int(get_state(connection, "google_cursor", "0") or 0)
        for offset in range(min(GOOGLE_BATCH_SIZE, len(query_pairs))):
            index = (cursor + offset) % len(query_pairs)
            section, query = query_pairs[index]
            source_key = f"google:{index}:{section}"
            items, error = fetch_google_news(section, query, window_start, now, max_items=15)
            gathered.extend(items)
            mark_health(
                connection,
                source_key,
                success=not error,
                item_count=len(items),
                error=error or "",
            )
            polled.append(source_key)
            if error:
                errors.append(f"{source_key}: {error}")
        set_state(connection, "google_cursor", str((cursor + GOOGLE_BATCH_SIZE) % len(query_pairs)))

        official_last = get_state(connection, "official_last")
        if due(official_last, 15, now):
            items, official_errors = fetch_official_feeds(window_start, now)
            gathered.extend(items)
            error_text = "; ".join(official_errors)
            mark_health(
                connection,
                "official-feeds",
                success=not official_errors,
                item_count=len(items),
                error=error_text,
            )
            polled.extend(f"official:{name}" for name in OFFICIAL_FEEDS)
            errors.extend(official_errors)
            set_state(connection, "official_last", now.isoformat())

        federal_cursor = int(get_state(connection, "federal_cursor", "0") or 0)
        term = FEDERAL_REGISTER_TERMS[federal_cursor % len(FEDERAL_REGISTER_TERMS)]
        items, federal_error = federal_register_term(term, window_start, now)
        gathered.extend(items)
        mark_health(
            connection,
            f"federal-register:{term}",
            success=not federal_error,
            item_count=len(items),
            error=federal_error or "",
        )
        polled.append(f"federal-register:{term}")
        if federal_error:
            errors.append(f"Federal Register {term}: {federal_error}")
        set_state(connection, "federal_cursor", str((federal_cursor + 1) % len(FEDERAL_REGISTER_TERMS)))

        inserted, refreshed = upsert_candidates(connection, gathered)
        cycle = {
            "status": "ok" if not errors else "partial",
            "started_at": now.isoformat(),
            "finished_at": utc_timestamp(),
            "polled": polled,
            "fetched": len(gathered),
            "inserted": inserted,
            "refreshed": refreshed,
            "errors": errors,
        }
        set_state(connection, "last_cycle", json.dumps(cycle, ensure_ascii=False))
        write_snapshots(connection, cycle)
        connection.commit()
        return cycle


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bounded deterministic five-minute monitor for Hermes ATU."
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print successful cycle details; scheduled runs are silent by default.",
    )
    arguments = parser.parse_args()
    try:
        result = run_cycle(dry_run=arguments.dry_run)
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    if arguments.dry_run or arguments.verbose or result.get("status") != "ok":
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
