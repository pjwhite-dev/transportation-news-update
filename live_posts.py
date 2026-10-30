"""The "From official sources" strip: the newest releases and posts straight from agencies and companies.

The wire's official sources (NASA, DoD, and the FAA, DOT and White House site searches) feed it as they poll,
and config `posts.sources` adds press-release feeds, Google News searches of an organization's own site, and
Bluesky accounts. Bluesky accounts are listed only when their handle is the organization's own domain, so an
impostor can never appear. X is not used: reading posts there needs a paid API.

Items live in store state (POSTS_KEY), newest first, trimmed to `keep_days`. Only the title, link and time are
kept; nothing on the strip is summarized or checked by a model.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

import requests

import news_engine
import web_discovery
from live_store import Store, iso, parse_iso

POSTS_KEY = "official_posts"
BLUESKY = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"

# Site searches return any page Google News indexed on the domain: careers, tools, contact pages.
_JUNK = re.compile(
    r"\b(?:intern(?:ship)?|careers?|jobs?|job opening|hiring|referral program|contact us|search results?|"
    r"tfrweb|log ?in|sign ?in|privacy|terms of|historical data|stock quote|investor relations|customer advisory|"
    r"carrier search|faq|cookie)\b",
    re.I,
)


def keep(title: str) -> bool:
    return len(title.split()) >= 5 and not title.startswith("- ") and not _JUNK.search(title)


def post_item(org: str, title: str, url: str, published: str, kind: str) -> dict[str, Any]:
    return {"org": org, "title": web_discovery.clean_spaces(title)[:220], "url": url, "published": published, "kind": kind}


def fetch_bluesky(handle: str, org: str, since: datetime, session: requests.Session | None = None) -> list[dict[str, Any]]:
    """An account's own recent posts (no replies, no reposts) from Bluesky's public API; no login."""
    response = (session or requests).get(BLUESKY, params={"actor": handle, "limit": 15, "filter": "posts_no_replies"}, timeout=20)
    response.raise_for_status()
    items = []
    for entry in response.json().get("feed", []):
        post = entry.get("post") or {}
        if entry.get("reason") or (post.get("author") or {}).get("handle") != handle:
            continue  # a repost of someone else
        record = post.get("record") or {}
        published = parse_iso(str(record.get("createdAt", "")).replace("Z", "+00:00"))
        text = " ".join(str(record.get("text", "")).split())
        if published is None or published < since or len(text.split()) < 5:
            continue
        rkey = str(post.get("uri", "")).rsplit("/", 1)[-1]
        items.append(post_item(org, text, f"https://bsky.app/profile/{handle}/post/{rkey}", iso(published), "Bluesky post"))
    return items


def from_wire_items(org: str, items: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    """Wire records (RSS or site search) as strip items, junk and off-topic ones left out."""
    out = []
    for item in items:
        title = str(item.get("title", ""))
        if not item.get("url") or not keep(title):
            continue
        out.append(post_item(org, title, str(item["url"]), str(item.get("published", "")), kind))
    return out


def relevant(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For organizations that also post about other things (NASA, airlines, carmakers): portfolio topics only."""
    return [i for i in items if news_engine.automated_record_is_portfolio_relevant({"title": i["title"], "source": i["org"]})]


def record(store: Store, items: list[dict[str, Any]], now: datetime, keep_days: float = 7) -> int:
    """Add new items (by link) and drop old ones; returns how many were new."""
    held = store.get_state(POSTS_KEY, []) or []
    known = {i["url"] for i in held}
    fresh = [i for i in items if i["url"] not in known]
    cutoff = now - timedelta(days=keep_days)
    merged = [i for i in held + fresh if (parse_iso(i.get("published", "")) or now) >= cutoff]
    merged.sort(key=lambda i: i.get("published", ""), reverse=True)
    store.set_state(POSTS_KEY, merged[:400])
    return len(fresh)


def public_posts(store: Store, now: datetime, limit: int = 15, per_org: int = 2) -> list[dict[str, Any]]:
    """Newest first, at most `per_org` from any one organization so a busy newsroom cannot fill the strip."""
    out, count = [], {}
    for item in store.get_state(POSTS_KEY, []) or []:
        published = parse_iso(item.get("published", ""))
        if published is None or published > now + timedelta(minutes=5):
            continue
        if count.get(item["org"], 0) >= per_org:
            continue
        count[item["org"]] = count.get(item["org"], 0) + 1
        out.append(item)
        if len(out) >= limit:
            break
    return out

