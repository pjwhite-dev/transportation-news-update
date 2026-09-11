from __future__ import annotations

import hashlib
import html
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import feedparser
import requests


HEADERS = {
    "User-Agent": (
        "TransportationNewsUpdate/5.0 "
        "(public-source daily briefing; contact via GitHub repository)"
    )
}

OFFICIAL_FEEDS = {
    "White House": "https://www.whitehouse.gov/briefing-room/feed/",
    "U.S. Department of Transportation": (
        "https://www.transportation.gov/rss/press-releases.xml"
    ),
    "Federal Aviation Administration": "https://www.faa.gov/newsroom/all_news.rss",
    "NASA": "https://www.nasa.gov/news-release/feed/",
    "U.S. Department of Defense": (
        "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?"
        "ContentType=1&Site=945&max=50"
    ),
}

SEARXNG_QUERIES = {
    "UAS and Drones": [
        "drone UAS BVLOS Part 108 Remote ID delivery manufacturing",
        "innovative drone use inspection agriculture emergency response",
    ],
    "UAS Security and C-UAS": [
        "counter-UAS C-UAS drone detection mitigation jamming interceptor",
        "airspace security unauthorized drone airport stadium infrastructure",
    ],
    "Military": [
        "military UAS drone procurement test deployment DARPA DIU",
        "Ukraine Russia battlefield drone strike unmanned warship",
    ],
    "eVTOL Integration Pilot Program and AAM": [
        "eVTOL eIPP advanced air mobility powered-lift FAA",
    ],
    "Autonomous Vehicles": [
        "autonomous vehicle robotaxi NHTSA FMCSA FMVSS Part 555",
        "driverless autonomous trucking deployment recall investigation",
    ],
    "Other Advanced Transportation": [
        "civil supersonic X-59 Boom Hermeus high-speed rail maglev hydrogen rail",
    ],
    "International": [
        "international drone eVTOL autonomous vehicle regulation deployment",
        "international high-speed rail maglev hydrogen train",
    ],
    "Federal Actions": [
        "site:dot.gov OR site:faa.gov OR site:nhtsa.gov advanced transportation",
        "site:fmcsa.dot.gov OR site:fra.dot.gov OR site:transit.dot.gov autonomy drone rail",
        "site:dhs.gov OR site:justice.gov OR site:tsa.gov counter-UAS drone security",
    ],
}

TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ocid",
    "ref",
    "source",
}


def clean_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def normalize_url(value: str) -> str:
    value = value.strip().strip("<>[](){}\"'").rstrip(".,;:!?")
    value = re.sub(r"^https?://msn\.om\b", "https://msn.com", value, flags=re.I)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in TRACKING_QUERY_KEYS
    ]
    return urlunparse(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", "", urlencode(query), "")
    )


def stable_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def source_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "Web source").removeprefix("www.")
    return host


def _published(value: str, fallback: datetime) -> datetime:
    if value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
            except (TypeError, ValueError, OverflowError):
                parsed = fallback
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=fallback.tzinfo)
        return parsed.astimezone(fallback.tzinfo)
    return fallback


def fetch_searxng(
    base_url: str,
    window_start: datetime,
    window_end: datetime,
    max_results_per_query: int = 25,
) -> tuple[list[dict[str, Any]], list[str]]:
    endpoint = base_url.rstrip("/") + "/search"
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    for section, queries in SEARXNG_QUERIES.items():
        for query in queries:
            try:
                response = requests.get(
                    endpoint,
                    params={
                        "q": query,
                        "format": "json",
                        "language": "en-US",
                        "time_range": "day",
                        "safesearch": 0,
                    },
                    headers=HEADERS,
                    timeout=45,
                )
                response.raise_for_status()
                results = response.json().get("results", [])
            except (requests.RequestException, ValueError) as exc:
                errors.append(f'SearXNG search "{query}": {exc}')
                continue
            for result in results[:max_results_per_query]:
                url = normalize_url(str(result.get("url", "")))
                title = clean_spaces(str(result.get("title", "")))
                if not url or not title:
                    continue
                published = _published(
                    str(result.get("publishedDate", "")), window_end
                )
                if published < window_start or published > window_end:
                    continue
                source = source_from_url(url)
                items.append(
                    {
                        "id": stable_id("searxng", url, title),
                        "search_section": section,
                        "title": title,
                        "summary": clean_spaces(str(result.get("content", "")))[:700],
                        "source": source,
                        "url": url,
                        "published": published.isoformat(),
                        "date_label": published.strftime("%b. %d, %Y").replace(" 0", " "),
                        "origin": "SearXNG",
                    }
                )
    return items, errors


def fetch_official_feeds(
    window_start: datetime,
    window_end: datetime,
) -> tuple[list[dict[str, Any]], list[str]]:
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    for source, url in OFFICIAL_FEEDS.items():
        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
        except requests.RequestException as exc:
            errors.append(f"Official feed {source}: {exc}")
            continue
        if getattr(feed, "bozo", False) and not feed.entries:
            errors.append(f"Official feed {source}: feed could not be parsed")
            continue
        for entry in feed.entries[:100]:
            published = _published(
                str(entry.get("published") or entry.get("updated") or ""),
                window_end,
            )
            if published < window_start or published > window_end:
                continue
            title = clean_spaces(str(entry.get("title", "")))
            article_url = normalize_url(str(entry.get("link", "")))
            if not title or not article_url:
                continue
            items.append(
                {
                    "id": stable_id("official", article_url, title),
                    "search_section": "Federal Actions",
                    "title": title,
                    "summary": clean_spaces(
                        re.sub(r"<[^>]+>", " ", str(entry.get("summary", "")))
                    )[:700],
                    "source": source,
                    "url": article_url,
                    "published": published.isoformat(),
                    "date_label": published.strftime("%b. %d, %Y").replace(" 0", " "),
                    "origin": "Official source RSS",
                }
            )
    return items, errors


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.description = ""
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "title":
            self._in_title = True
        if tag.casefold() != "meta":
            return
        values = {key.casefold(): value or "" for key, value in attrs}
        label = (values.get("property") or values.get("name") or "").casefold()
        content = clean_spaces(values.get("content", ""))
        if label in {"og:title", "twitter:title"} and content and not self.title:
            self.title = content
        elif label in {"description", "og:description", "twitter:description"} and content and not self.description:
            self.description = content

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "title":
            self._in_title = False
            if not self.title:
                self.title = clean_spaces("".join(self._title_parts))

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)


def enrich_article_metadata(
    items: list[dict[str, Any]],
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    limit = limit if limit is not None else int(os.environ.get("ARTICLE_METADATA_FETCH_LIMIT", "120"))
    errors: list[str] = []
    for item in items[: max(0, limit)]:
        url = normalize_url(str(item.get("url", "")))
        if not url:
            continue
        try:
            response = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").casefold()
            resolved = normalize_url(response.url)
            if resolved:
                item["url"] = resolved
            if "html" not in content_type:
                continue
            parser = _MetadataParser()
            parser.feed(response.text[:1_500_000])
            title = clean_spaces(parser.title)
            if title and len(title) >= 12:
                item["original_title"] = title
                item["title"] = title
            if parser.description and not item.get("summary"):
                item["summary"] = parser.description[:700]
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"Article metadata {source_from_url(url)}: {exc}")
    return items, errors
