from __future__ import annotations

import html
import re
from urllib.parse import urlparse, urlunparse

import requests

from news_engine import clean_spaces, stable_id


MANUAL_IMPORT_USER_AGENT = (
    "TransportationNewsUpdate/4.0 manual-intake metadata fetcher"
)
URL_PATTERN = re.compile(
    r"(?:https?://|www\.)[^\s<>\"'`]+",
    re.IGNORECASE,
)
SOURCE_ONLY_LABELS = {
    "msn",
    "msn.com",
    "aol",
    "aol.com",
    "yahoo",
    "yahoo finance",
    "google news",
    "reuters",
    "associated press",
    "ap",
    "read more",
    "click here",
    "article",
    "link",
}


def normalize_import_url(value: str) -> str:
    """Remove common email/Markdown wrappers and trailing prose punctuation."""
    value = html.unescape(value or "").strip()
    previous = None
    while value and value != previous:
        previous = value
        value = re.sub(r"[.,;:!?]+$", "", value)
        value = value.strip("<>[](){}\"'`")
    if value.lower().startswith("www."):
        value = "https://" + value
    return value


def url_identity(url: str) -> str:
    """Return a stable comparison key while preserving the displayed URL."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return url.casefold()
    return urlunparse(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            parsed.path,
            parsed.params,
            parsed.query,
            "",
        )
    )


def source_from_url(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return "Supplemental source"
    host = host.lower().removeprefix("www.")
    brand = host.split(".")[0].replace("-", " ").strip()
    known = {
        "msn": "MSN",
        "aol": "AOL",
        "finance": "Yahoo Finance",
        "news": "Google News",
    }
    return known.get(brand, brand.title() or "Supplemental source")


def is_source_only(value: str, source: str = "") -> bool:
    cleaned = clean_spaces(re.sub(r"^[•\-–—\s]+", "", value or "")).strip(
        " :|"
    )
    if not cleaned:
        return True
    lowered = cleaned.casefold()
    if lowered in SOURCE_ONLY_LABELS:
        return True
    if source and lowered == source.casefold():
        return True
    if lowered.startswith(("http://", "https://")):
        return True
    return len(cleaned) < 8


def clean_headline_candidate(value: str, source: str = "") -> str:
    value = re.sub(r"https?://\S+", " ", value or "")
    value = clean_spaces(value)
    value = re.sub(r"^[•\-–—\d.)\s]+", "", value)
    value = value.strip("<>[](){}\"'` ")
    if source:
        value = re.sub(
            rf"\s*[-|–—]\s*{re.escape(source)}\s*$",
            "",
            value,
            flags=re.IGNORECASE,
        )
    return clean_spaces(value)


def context_for_link(lines: list[str], index: int, url: str) -> tuple[str, str]:
    same_line = clean_headline_candidate(lines[index].replace(url, ""))
    context_lines = []
    for offset in (0, -1, -2, 1):
        pos = index + offset
        if 0 <= pos < len(lines):
            candidate = clean_spaces(lines[pos])
            if candidate and candidate not in context_lines:
                context_lines.append(candidate)

    context = " ".join(context_lines)[:1200]
    if same_line and not is_source_only(same_line):
        return same_line, context

    for offset in (-1, -2, 1):
        pos = index + offset
        if 0 <= pos < len(lines):
            candidate = clean_headline_candidate(lines[pos])
            if candidate and not is_source_only(candidate):
                return candidate, context

    return "", context


def first_html_match(document: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, document, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return clean_spaces(
                html.unescape(re.sub(r"<[^>]+>", " ", match.group(1)))
            )
    return ""


def fetch_link_metadata(record: dict) -> dict:
    enriched = dict(record)
    try:
        response = requests.get(
            record["url"],
            headers={"User-Agent": MANUAL_IMPORT_USER_AGENT},
            timeout=18,
            allow_redirects=True,
        )
        response.raise_for_status()
        document = response.text[:750000]
        final_url = normalize_import_url(response.url)
        source = source_from_url(final_url)

        fetched_title = first_html_match(
            document,
            [
                r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']',
                r'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']og:title["\']',
                r"<title[^>]*>(.*?)</title>",
            ],
        )
        description = first_html_match(
            document,
            [
                r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
                r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
                r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
            ],
        )

        pasted = clean_headline_candidate(
            enriched.get("pasted_headline", ""), source
        )
        fetched = clean_headline_candidate(fetched_title, source)
        if pasted and not is_source_only(pasted, source):
            title = pasted
        elif fetched and not is_source_only(fetched, source):
            title = fetched
        else:
            title = clean_headline_candidate(
                enriched.get("pasted_context", ""), source
            )

        enriched.update(
            {
                "url": final_url or record["url"],
                "title": title[:260],
                "original_title": fetched_title[:260],
                "description": description[:1200],
                "source": source,
                "fetch_status": "Metadata retrieved",
            }
        )
    except Exception as exc:
        source = source_from_url(record["url"])
        pasted = clean_headline_candidate(
            record.get("pasted_headline", ""), source
        )
        context_title = clean_headline_candidate(
            record.get("pasted_context", ""), source
        )
        title = (
            pasted
            if pasted and not is_source_only(pasted, source)
            else context_title
        )
        enriched.update(
            {
                "source": source,
                "title": title[:260],
                "description": "",
                "fetch_status": (
                    "Metadata unavailable: "
                    + clean_spaces(str(exc))[:160]
                ),
            }
        )
    return enriched


def extract_supplemental_items(
    raw_text: str,
    fetch_metadata: bool = True,
) -> list[dict]:
    lines = [html.unescape(line.rstrip()) for line in (raw_text or "").splitlines()]
    records: list[dict] = []
    seen: set[str] = set()

    for index, line in enumerate(lines):
        for match in URL_PATTERN.finditer(line):
            url = normalize_import_url(match.group(0))
            original_identity = url_identity(url)
            if not url or original_identity in seen:
                continue

            pasted_headline, context = context_for_link(
                lines,
                index,
                match.group(0),
            )
            record = {
                "id": stable_id("supplemental", url),
                "url": url,
                "title": pasted_headline,
                "pasted_headline": pasted_headline,
                "pasted_context": context,
                "summary": "",
                "description": "",
                "source": source_from_url(url),
                "origin": "Supplemental daily email",
                "required_include": True,
                "fetch_status": "Not fetched",
            }
            if fetch_metadata:
                record = fetch_link_metadata(record)

            final_identity = url_identity(record["url"])
            if final_identity in seen:
                continue
            seen.update({original_identity, final_identity})
            records.append(record)

    return records
