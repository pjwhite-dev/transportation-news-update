from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import unquote, urlparse, urlunparse

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
URL_CONTINUATION_PATTERN = re.compile(
    r"[A-Za-z0-9._~:/?#@!$&'()*+,;=%\-\[\]]+>?$"
)
HTML_BODY_PATTERN = re.compile(r"<(?:html|body|p|div|a|br)\b", re.IGNORECASE)


def join_wrapped_url_lines(lines: list[str]) -> list[str]:
    """Rejoin URLs hard-wrapped by Outlook's plain-text conversion."""
    joined: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        while index + 1 < len(lines):
            matches = list(URL_PATTERN.finditer(line))
            if not matches or matches[-1].end() != len(line):
                break
            next_line = lines[index + 1].strip()
            current_url = matches[-1].group(0)
            if (
                len(current_url) < 60
                or not next_line
                or next_line.lower().startswith(("http://", "https://", "www."))
                or not URL_CONTINUATION_PATTERN.fullmatch(next_line)
            ):
                break
            line += next_line
            index += 1
        joined.append(line)
        index += 1
    return joined


class SupplementalBodyParser(HTMLParser):
    """Turn email HTML into text lines while preserving actual anchor targets."""

    BLOCK_TAGS = {"address", "blockquote", "div", "li", "p", "table", "tr", "td"}
    SUPPRESSED_TAGS = {"head", "script", "style"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed_depth = 0
        self.anchor_href = ""
        self.anchor_text: list[str] = []

    def _break(self) -> None:
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.casefold()
        if tag in self.SUPPRESSED_TAGS:
            self.suppressed_depth += 1
            return
        if self.suppressed_depth:
            return
        if tag in self.BLOCK_TAGS or tag == "br":
            self._break()
        if tag == "a":
            attributes = {key.casefold(): value or "" for key, value in attrs}
            href = html.unescape(attributes.get("href", "")).strip()
            self.anchor_href = href if href.lower().startswith(("http://", "https://")) else ""
            self.anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in self.SUPPRESSED_TAGS:
            self.suppressed_depth = max(0, self.suppressed_depth - 1)
            return
        if self.suppressed_depth:
            return
        if tag == "a" and self.anchor_href:
            anchor_text = clean_spaces("".join(self.anchor_text))
            if anchor_text and not URL_PATTERN.fullmatch(anchor_text):
                self.parts.append(anchor_text)
            self._break()
            self.parts.append(self.anchor_href)
            self._break()
            self.anchor_href = ""
            self.anchor_text = []
        if tag in self.BLOCK_TAGS:
            self._break()

    def handle_data(self, data: str) -> None:
        if self.suppressed_depth:
            return
        if self.anchor_href:
            self.anchor_text.append(data)
        else:
            self.parts.append(data)

    def lines(self) -> list[str]:
        return [line.rstrip() for line in "".join(self.parts).splitlines()]


def supplemental_email_lines(raw_text: str) -> list[str]:
    """Normalize HTML or plain-text email into URL-safe contextual lines."""
    value = raw_text or ""
    if HTML_BODY_PATTERN.search(value):
        parser = SupplementalBodyParser()
        parser.feed(value)
        lines = parser.lines()
    else:
        lines = [html.unescape(line.rstrip()) for line in value.splitlines()]
    return join_wrapped_url_lines(lines)


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


def headline_is_sentence_fragment(value: str) -> bool:
    value = clean_spaces(value)
    if not value:
        return False
    words = re.findall(r"[A-Za-z0-9]+", value.casefold())
    starts_as_fragment = (
        value[:1].islower()
        and not value.startswith(("eVTOL", "iPhone", "xAI"))
    )
    incomplete_endings = {
        "a",
        "an",
        "and",
        "against",
        "at",
        "by",
        "for",
        "from",
        "in",
        "of",
        "on",
        "over",
        "the",
        "to",
        "with",
    }
    return starts_as_fragment or bool(words and words[-1] in incomplete_endings)


def headline_from_url_slug(url: str) -> str:
    try:
        segments = [unquote(segment) for segment in urlparse(url).path.split("/") if segment]
    except ValueError:
        return ""
    for segment in reversed(segments):
        if re.fullmatch(r"ar-[A-Za-z0-9]+", segment, re.IGNORECASE):
            continue
        candidate = re.sub(r"-\d{5,}$", "", segment)
        if candidate.count("-") < 3 or len(candidate) < 25:
            continue
        value = clean_spaces(candidate.replace("-", " "))
        for acronym in (
            "AI",
            "CIA",
            "FAA",
            "UAS",
            "UAV",
            "NHTSA",
            "DARPA",
            "DOD",
            "US",
        ):
            value = re.sub(rf"\b{acronym}\b", acronym, value, flags=re.IGNORECASE)
        for proper_name in (
            "Army", "Europe", "Russia", "Russian", "Ukraine", "Zubr"
        ):
            value = re.sub(rf"\b{proper_name}\b", proper_name, value, flags=re.IGNORECASE)
        return value[:1].upper() + value[1:]
    return ""


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
    if headline_is_sentence_fragment(candidate):
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
    if len(re.findall(r"[.!?](?:\s|$)", candidate)) > 1:
        return False
    return True


def headline_candidate_score(candidate: str, offset: int) -> int:
    """Prefer headline-shaped lines over nearby descriptive sentences."""
    score = max(0, 12 - abs(offset))
    if candidate.endswith("."):
        score -= 6
    else:
        score += 2
    if re.match(r"^[A-Z0-9][^:]{1,24}:\s+\S", candidate):
        score += 4
    return score


def context_for_link(lines: list[str], index: int, url: str) -> tuple[str, str]:
    source = source_from_url(normalize_import_url(url))
    same_line = clean_headline_candidate(lines[index], source)
    context_lines = []
    for offset in (0, -1, -2, -3, -4, -5, -6, 1):
        pos = index + offset
        if 0 <= pos < len(lines):
            candidate = clean_headline_candidate(lines[pos], source)
            if candidate and candidate not in context_lines:
                context_lines.append(candidate)

    context = " ".join(context_lines)[:1200]
    # A headline normally precedes a pasted source/description/link block. Prefer
    # those lines to prose that happens to share the URL's line.
    candidates: list[tuple[int, str]] = []
    for offset in (-1, -2, -3, -4, -5, -6):
        pos = index + offset
        if 0 <= pos < len(lines):
            candidate = clean_headline_candidate(lines[pos], source)
            if is_likely_headline(candidate, source):
                candidates.append((headline_candidate_score(candidate, offset), candidate))
    if candidates:
        return max(candidates, key=lambda pair: pair[0])[1], context

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
            timeout=12,
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
        slug_title = headline_from_url_slug(final_url or record["url"])
        # The linked article's own metadata is authoritative. Nearby pasted
        # text is only a fallback because it may be a description or quotation.
        if fetched and not is_source_only(fetched, source):
            title = fetched
        elif is_likely_headline(pasted, source):
            title = pasted
        elif slug_title:
            title = slug_title
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
        slug_title = headline_from_url_slug(record["url"])
        if is_likely_headline(pasted, source):
            title = pasted
        elif slug_title:
            title = slug_title
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
    lines = supplemental_email_lines(raw_text)
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
            final_identity = url_identity(record["url"])
            if final_identity in seen:
                continue
            seen.update({original_identity, final_identity})
            records.append(record)

    if fetch_metadata and records:
        worker_count = min(8, len(records))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            enriched_records = list(executor.map(fetch_link_metadata, records))
        deduplicated: list[dict] = []
        seen_final: set[str] = set()
        for record in enriched_records:
            final_identity = url_identity(record["url"])
            if final_identity in seen_final:
                continue
            seen_final.add(final_identity)
            deduplicated.append(record)
        return deduplicated

    return records
