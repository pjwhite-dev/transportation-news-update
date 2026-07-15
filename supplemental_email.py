from __future__ import annotations

import html
import re
from html.parser import HTMLParser
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
    value = URL_PATTERN.sub(" ", html.unescape(value or ""))
    value = re.sub(r"<>|<\s*>|[<>]", " ", value)
    value = clean_spaces(value)
    value = re.sub(r"^[•\-–—]\s*", "", value)
    value = re.sub(r"^\d{1,2}[.)]\s+", "", value)
    value = value.strip("<>[](){}\"'` ")
    if source:
        value = re.sub(
            rf"\s*[-|–—]\s*{re.escape(source)}\s*$",
            "",
            value,
            flags=re.IGNORECASE,
        )
    return clean_spaces(value)


def is_likely_headline(value: str, source: str = "") -> bool:
    """Reject source labels and obvious prose fragments used as link context."""
    candidate = clean_headline_candidate(value, source)
    if not candidate or is_source_only(candidate, source):
        return False
    words = candidate.split()
    if len(candidate) > 240 or len(words) > 28:
        return False
    lowered = candidate.casefold()
    prose_openings = (
        "it's ", "it is ", "this is ", "there is ", "there are ",
        "we are ", "we're ", "they are ", "they're ",
    )
    if len(candidate) > 70 and lowered.startswith(prose_openings):
        return False
    if len(candidate) > 120 and candidate.endswith((".", "?", "!")):
        return False
    return True


def context_for_link(lines: list[str], index: int, url: str) -> tuple[str, str]:
    source = source_from_url(normalize_import_url(url))
    same_line = clean_headline_candidate(lines[index], source)
    context_lines = []
    for offset in (0, -1, -2, 1):
        pos = index + offset
        if 0 <= pos < len(lines):
            candidate = clean_headline_candidate(lines[pos], source)
            if candidate and candidate not in context_lines:
                context_lines.append(candidate)

    context = " ".join(context_lines)[:1200]
    # A headline normally precedes a pasted source/description/link block. Prefer
    # those lines to prose that happens to share the URL's line.
    for offset in (-1, -2):
        pos = index + offset
        if 0 <= pos < len(lines):
            candidate = clean_headline_candidate(lines[pos], source)
            if is_likely_headline(candidate, source):
                return candidate, context

    if is_likely_headline(same_line, source):
        return same_line, context

    pos = index + 1
    if pos < len(lines):
        candidate = clean_headline_candidate(lines[pos], source)
        if is_likely_headline(candidate, source):
            return candidate, context

    return "", context


class ArticleMetadataParser(HTMLParser):
    """Extract title metadata without breaking on quotes inside attribute values."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metadata: dict[str, str] = {}
        self.in_title = False
        self.title_parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.casefold() == "title":
            self.in_title = True
            return
        if tag.casefold() != "meta":
            return
        attributes = {
            key.casefold(): clean_spaces(value or "") for key, value in attrs
        }
        key = (attributes.get("property") or attributes.get("name") or "").casefold()
        content = attributes.get("content", "")
        if key and content and key not in self.metadata:
            self.metadata[key] = content

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title and clean_spaces(data):
            self.title_parts.append(data)


def parse_article_metadata(document: str) -> tuple[str, str]:
    parser = ArticleMetadataParser()
    parser.feed(document)
    title = (
        parser.metadata.get("og:title")
        or parser.metadata.get("twitter:title")
        or clean_spaces(" ".join(parser.title_parts))
    )
    description = (
        parser.metadata.get("og:description")
        or parser.metadata.get("twitter:description")
        or parser.metadata.get("description")
        or ""
    )
    return clean_spaces(title), clean_spaces(description)


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

        fetched_title, description = parse_article_metadata(document)

        pasted = clean_headline_candidate(
            enriched.get("pasted_headline", ""), source
        )
        fetched = clean_headline_candidate(fetched_title, source)
        # The linked article's own metadata is authoritative. Nearby pasted
        # text is only a fallback because it may be a description or quotation.
        if fetched and not is_source_only(fetched, source):
            title = fetched
        elif is_likely_headline(pasted, source):
            title = pasted
        else:
            context_title = clean_headline_candidate(
                enriched.get("pasted_context", ""), source
            )
            title = (
                context_title
                if is_likely_headline(context_title, source)
                else "Headline unavailable — review this link"
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
        if is_likely_headline(pasted, source):
            title = pasted
        elif is_likely_headline(context_title, source):
            title = context_title
        else:
            title = "Headline unavailable — review this link"
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
                "editor_vetted": True,
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
