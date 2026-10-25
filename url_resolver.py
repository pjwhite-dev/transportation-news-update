"""Resolve Google News redirect wrappers to publisher URLs (rebuild plan 4.9, Phase 1 item 3).

news.google.com/rss/articles/<id> links are not publisher URLs. Older ids embed the
publisher URL in base64; newer ones ("AU_yqL...") must be exchanged through Google's
batchexecute endpoint using a signature scraped from the article page. Results are
cached on disk so a rerun resolves nothing twice. Resolution failures return None;
callers must drop the candidate rather than publish a wrapper.
"""

from __future__ import annotations

import base64
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import requests

USER_AGENT = "Mozilla/5.0 (compatible; AdvancedTransportationNewsBot/1.0)"
BATCHEXECUTE_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
GOOGLE_NEWS_HOSTS = {"news.google.com"}
_ID_PATTERN = re.compile(r"/(?:rss/)?(?:articles|read)/([A-Za-z0-9_-]+)")


def is_google_news_url(url: str) -> bool:
    return urlparse(url.strip()).netloc.casefold() in GOOGLE_NEWS_HOSTS


def article_id(url: str) -> str | None:
    match = _ID_PATTERN.search(urlparse(url).path)
    return match.group(1) if match else None


def decode_embedded_url(identifier: str) -> str | None:
    """Old-style ids carry the publisher URL directly; returns None for the new format."""
    try:
        raw = base64.urlsafe_b64decode(identifier + "=" * (-len(identifier) % 4))
    except (ValueError, TypeError):
        return None
    prefix, suffix = b"\x08\x13\x22", b"\xd2\x01\x00"
    if raw.startswith(prefix):
        raw = raw[len(prefix):]
    if raw.endswith(suffix):
        raw = raw[: -len(suffix)]
    if not raw:
        return None
    length = raw[0]
    start = 1
    if length >= 0x80:
        if len(raw) < 2:
            return None
        length = (length & 0x7F) | (raw[1] << 7)
        start = 2
    text = raw[start : start + length].decode("utf-8", errors="ignore")
    if text.startswith(("http://", "https://")):
        return text
    return None


def _scrape_signature(identifier: str, session: requests.Session) -> tuple[str, str] | None:
    response = session.get(
        f"https://news.google.com/rss/articles/{identifier}",
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    if response.status_code != 200:
        return None
    signature = re.search(r'data-n-a-sg="([^"]+)"', response.text)
    timestamp = re.search(r'data-n-a-ts="([^"]+)"', response.text)
    if not signature or not timestamp:
        return None
    return signature.group(1), timestamp.group(1)


def exchange_for_url(identifier: str, session: requests.Session) -> str | None:
    scraped = _scrape_signature(identifier, session)
    if scraped is None:
        return None
    signature, timestamp = scraped
    inner = (
        '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
        f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{identifier}",{timestamp},"{signature}"]'
    )
    body = "f.req=" + quote(json.dumps([[["Fbv4je", inner]]]))
    response = session.post(
        BATCHEXECUTE_URL,
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "User-Agent": USER_AGENT,
        },
        data=body,
        timeout=20,
    )
    if response.status_code != 200:
        return None
    try:
        parsed = json.loads(response.text.split("\n\n")[1])[:-2]
        url = json.loads(parsed[0][2])[1]
    except (IndexError, KeyError, TypeError, ValueError):
        return None
    return url if isinstance(url, str) and url.startswith(("http://", "https://")) else None


def resolve_url(url: str, session: requests.Session | None = None) -> str | None:
    """Publisher URL for a Google News wrapper; non-wrapper URLs are returned unchanged."""
    if not is_google_news_url(url):
        return url
    identifier = article_id(url)
    if not identifier:
        return None
    embedded = decode_embedded_url(identifier)
    if embedded:
        return embedded
    try:
        return exchange_for_url(identifier, session or requests.Session())
    except requests.RequestException:
        return None


def load_cache(path: Path) -> dict[str, str]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def resolve_articles(
    articles: list[dict[str, Any]],
    cache_path: Path,
    *,
    workers: int = 6,
    delay_seconds: float = 0.15,
    session_factory: Any = requests.Session,
) -> dict[str, int]:
    """Replace wrapper URLs in-place; mark failures with ``url_unresolved``.

    The original wrapper is kept as ``aggregator_url``. Returns counts for the run log.
    """
    wrappers = [a for a in articles if is_google_news_url(str(a.get("url", "")))]
    if not wrappers:
        return {"wrappers": 0, "resolved": 0, "unresolved": 0}
    cache = load_cache(cache_path)
    pending = sorted({a["url"] for a in wrappers if a["url"] not in cache})

    def work(url: str) -> tuple[str, str | None]:
        time.sleep(delay_seconds)
        return url, resolve_url(url, session_factory())

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for url, resolved in pool.map(work, pending):
            if resolved:
                cache[url] = resolved
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=1, sort_keys=True), encoding="utf-8")

    resolved_count = 0
    for article in wrappers:
        wrapper = article["url"]
        article["aggregator_url"] = wrapper
        publisher = cache.get(wrapper)
        if publisher and not is_google_news_url(publisher):
            article["url"] = publisher
            resolved_count += 1
        else:
            article["url_unresolved"] = True
    return {"wrappers": len(wrappers), "resolved": resolved_count,
            "unresolved": len(wrappers) - resolved_count}
