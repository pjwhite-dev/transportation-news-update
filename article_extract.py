"""Reads the article itself: real publication date, clean headline, body text, article-ness.

Deterministic. The only network call is fetching the candidate's own page (an honest, identified
User-Agent, one request per URL). Every decision returns a reason so the QA report can show it.
"""

from __future__ import annotations

import gzip
import hashlib
import html as htmllib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests

from calendar_rules import freshness_hours
from editorial_config import compiled, load_config

EASTERN = ZoneInfo("America/New_York")
USER_AGENT = "AdvancedTransportationNewsBot/1.0 (+https://news.peterjwhite.org)"
MAX_FUTURE = timedelta(days=2)
BODY_CHARS = 8000

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], start=1)}
_MONTH_DATE = re.compile(
    r"\b(" + "|".join(m[:3] + r"[a-z]*\.?" for m in _MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d\d)\b", re.I)
_DAY_FIRST = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(m[:3] + r"[a-z]*\.?" for m in _MONTHS) + r")\s*,?\s+(20\d\d)\b", re.I)
_LABELLED = re.compile(
    r"(?:first published|published(?: on| at)?|posted(?: on)?|publication date|date published|originally published)"
    r"\s*[:\-\u2013|]?\s*([^\n|]{6,40})", re.I)
_SLASH_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d\d)\b")
_URL_DATES = (
    re.compile(r"/(20\d\d)/(\d{1,2})/(\d{1,2})(?:/|$)"),
    re.compile(r"/(20\d\d)-(\d{2})-(\d{2})(?:[/-]|$)"),
    re.compile(r"[/_-](20\d\d)(\d{2})(\d{2})(?:[/_.-]|$)"),
)
_DATE_META_KEYS = (
    "article:published_time", "og:article:published_time", "og:published_time", "datepublished", "date",
    "pubdate", "publishdate", "publish-date", "publication_date", "dc.date", "dc.date.issued", "dcterms.created",
    "parsely-pub-date", "sailthru.date", "article.published", "cxenseparse:recs:publishtime",
)
_ARTICLE_TYPES = ("article", "newsarticle", "reportagenewsarticle", "blogposting", "report", "analysisnewsarticle")
_SKIP_TAGS = {"script", "style", "nav", "footer", "aside", "header", "form", "noscript", "figcaption", "svg", "button", "select"}


@dataclass
class PageFacts:
    headline: str = ""
    headline_source: str = ""
    published: datetime | None = None
    date_source: str = ""
    body_text: str = ""
    word_count: int = 0
    h1_count: int = 0
    site_name: str = ""
    original_publisher: str = ""
    is_article_type: bool = False
    links: list[tuple[str, str]] = field(default_factory=list)


def parse_date(text: str, *, assume_hour: int = 12) -> datetime | None:
    """ISO, RFC 2822, 'October 6, 2026' and '06/15/2026' to an aware ET datetime (date-only means noon ET)."""
    value = " ".join(str(text or "").split())
    if not value:
        return None
    candidates = [value.replace("Z", "+00:00"), re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value.replace("Z", "+00:00"))]
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return parsed.replace(hour=assume_hour, tzinfo=EASTERN)
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=EASTERN)).astimezone(EASTERN)
    try:
        parsed = parsedate_to_datetime(value)
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(EASTERN)
    except (TypeError, ValueError, IndexError):
        pass
    found = _MONTH_DATE.search(value)
    if found:
        month = _MONTHS.get(next((m for m in _MONTHS if m.startswith(found.group(1)[:3].lower())), ""), 0)
        try:
            return datetime(int(found.group(3)), month, int(found.group(2)), assume_hour, tzinfo=EASTERN)
        except ValueError:
            return None
    found = _DAY_FIRST.search(value)
    if found:
        month = _MONTHS.get(next((m for m in _MONTHS if m.startswith(found.group(2)[:3].lower())), ""), 0)
        try:
            return datetime(int(found.group(3)), month, int(found.group(1)), assume_hour, tzinfo=EASTERN)
        except ValueError:
            return None
    found = _SLASH_DATE.search(value)
    if found:
        try:
            return datetime(int(found.group(3)), int(found.group(1)), int(found.group(2)), assume_hour, tzinfo=EASTERN)
        except ValueError:
            return None
    return None


def date_from_url(url: str) -> datetime | None:
    path = urlparse(url).path
    for pattern in _URL_DATES:
        match = pattern.search(path)
        if match:
            try:
                return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), 12, tzinfo=EASTERN)
            except ValueError:
                continue
    return None


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self.h1: list[str] = []
        self.times: list[tuple[str, bool, bool]] = []  # (datetime attribute, looks like the publication date, inside <article>)
        self.text: list[str] = []
        self.json_ld: list[str] = []
        self.paragraphs: list[tuple[str, bool]] = []  # (text, inside <article>/<main>)
        self.links: list[tuple[str, str]] = []
        self.canonical = ""
        self._skip = 0
        self._stack: list[str] = []
        self._buffer: list[str] = []
        self._capture = ""
        self._in_article = 0
        self._ld = False
        self._ld_buffer: list[str] = []
        self._link_href = ""
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "script" and "ld+json" in a.get("type", "").lower():
            self._ld, self._ld_buffer = True, []
        if tag in _SKIP_TAGS:
            self._skip += 1
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").strip().lower()
            if key and a.get("content"):
                self.meta.setdefault(key, a["content"].strip())
        elif tag == "link" and a.get("rel", "").lower() == "canonical":
            self.canonical = a.get("href", "")
        elif tag == "time" and a.get("datetime"):
            hint = " ".join([a.get("itemprop", ""), a.get("class", ""), a.get("pubdate", "")]).lower()
            self.times.append((a["datetime"], "publish" in hint or "pubdate" in hint or "datepublished" in hint, self._in_article > 0))
        elif tag in ("article", "main"):
            self._in_article += 1
        elif tag in ("title", "h1", "p") and not self._skip:
            self._capture, self._buffer = tag, []
        elif tag == "a" and a.get("href") and not self._skip:
            self._link_href, self._link_text = a["href"], []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._ld:
            self.json_ld.append("".join(self._ld_buffer))
            self._ld = False
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        if tag in ("article", "main"):
            self._in_article = max(0, self._in_article - 1)
        if tag == self._capture:
            text = " ".join("".join(self._buffer).split())
            if tag == "title":
                self.title = text
            elif tag == "h1" and text:
                self.h1.append(text)
            elif tag == "p" and text:
                self.paragraphs.append((text, self._in_article > 0))
            self._capture = ""
        if tag == "a" and self._link_href:
            text = " ".join("".join(self._link_text).split())
            if text:
                self.links.append((self._link_href, text))
            self._link_href = ""

    def handle_data(self, data: str) -> None:
        if not self._skip and not self._ld and data.strip():
            self.text.append(" ".join(data.split()))
        if self._ld:
            self._ld_buffer.append(data)
        if self._capture:
            self._buffer.append(data)
        if self._link_href:
            self._link_text.append(data)


def _walk_json(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_json(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_json(value)


def _ld_type_is_article(node: dict[str, Any]) -> bool:
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return any(str(k).lower() in _ARTICLE_TYPES for k in kinds if k)


def _publisher_name(node: dict[str, Any]) -> str:
    publisher = node.get("publisher")
    if isinstance(publisher, dict):
        return str(publisher.get("name", ""))
    return str(publisher or "") if isinstance(publisher, str) else ""


_BYLINE_DATE = re.compile(
    r"\b20\d\d-\d{2}-\d{2}(?:[ T]\d{2}:\d{2})?\b|\b\d{1,2}/\d{1,2}/20\d\d\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+20\d\d\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+20\d\d\b",
    re.I,
)


def byline_date(chunks: list[str], headline: str, window: int = 25) -> datetime | None:
    """A date printed in the few text lines right after the headline (the byline), if there is one."""
    key = " ".join(headline.split()).casefold()[:24]
    if len(key) < 12:
        return None
    for index, chunk in enumerate(chunks):
        if chunk.casefold().startswith(key):
            for follower in chunks[index + 1: index + 1 + window]:
                found = _BYLINE_DATE.search(follower)
                parsed = parse_date(found.group(0)) if found else None
                if parsed:
                    return parsed
            return None
    return None


def extract_page(html: str, url: str = "") -> PageFacts:
    parser = _PageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # malformed markup must never crash the run
        pass
    facts = PageFacts()
    ld_nodes: list[dict[str, Any]] = []
    for block in parser.json_ld:
        try:
            ld_nodes.extend(n for n in _walk_json(json.loads(block)) if _ld_type_is_article(n))
        except ValueError:
            continue
    facts.is_article_type = bool(ld_nodes) or parser.meta.get("og:type", "").lower() == "article"

    # Publication date, strongest evidence first. The crawl date and feed date are never used.
    for node in ld_nodes:
        parsed = parse_date(str(node.get("datePublished", "")))
        if parsed:
            facts.published, facts.date_source = parsed, "json-ld datePublished"
            break
    if not facts.published:
        for key in _DATE_META_KEYS:
            parsed = parse_date(parser.meta.get(key, ""))
            if parsed:
                facts.published, facts.date_source = parsed, f"meta {key}"
                break
    if not facts.published:
        # A visible 'Published: 6 October 2026' line near the headline beats an unlabeled <time>, which is
        # often a related-story widget. Look in the first part of the page text only.
        head_text = " ".join(parser.text[:400])
        for match in _LABELLED.finditer(head_text):
            parsed = parse_date(match.group(1))
            if parsed:
                facts.published, facts.date_source = parsed, "labelled 'Published' text"
                break
    if not facts.published:
        # Some publishers put 'Published: Sun, Oct 25, 2026' in the page title or description only.
        for key in ("og:title", "twitter:title", "og:description", "description", "twitter:description"):
            match = _LABELLED.search(parser.meta.get(key, ""))
            parsed = parse_date(match.group(1)) if match else None
            if parsed:
                facts.published, facts.date_source = parsed, "labelled 'Published' in page title"
                break
    if not facts.published:
        early = next((h for h in [*(str(n.get("headline", "")) for n in ld_nodes), parser.meta.get("og:title", ""),
                                  parser.h1[0] if parser.h1 else ""] if " ".join(h.split())), "")
        parsed = byline_date(parser.text, early)
        if parsed:
            facts.published, facts.date_source = parsed, "byline beside the headline"
    if not facts.published:
        stamped = [(v, hint, inside) for v, hint, inside in parser.times if parse_date(v)]
        pick = next((v for v, hint, _ in stamped if hint), None) or next((v for v, _, inside in stamped if inside), None)
        if pick is None and stamped and len({parse_date(v).date() for v, _, _ in stamped}) == 1:
            pick = stamped[0][0]  # every <time> on the page agrees, so it is not a mixed related-stories widget
        if pick:
            facts.published, facts.date_source = parse_date(pick), "<time datetime>"
    if not facts.published:
        parsed = date_from_url(url)
        if parsed:
            facts.published, facts.date_source = parsed, "date in URL"
    if not facts.published and "congress.gov" in url:
        text = " ".join(t for t, _ in parser.paragraphs)
        match = re.search(r"(?:Reported|Latest Action|Date)\D{0,12}(\d{1,2}/\d{1,2}/20\d\d)", text)
        parsed = parse_date(match.group(1)) if match else None
        if parsed:
            facts.published, facts.date_source = parsed, "congress.gov record date"

    # Headline: JSON-LD, then og:title, then the single <h1>, then <title>.
    facts.site_name = parser.meta.get("og:site_name", "") or next((_publisher_name(n) for n in ld_nodes if _publisher_name(n)), "")
    candidates = [(str(n.get("headline", "")), "json-ld headline") for n in ld_nodes]
    candidates += [(parser.meta.get("og:title", ""), "og:title"), (parser.h1[0] if parser.h1 else "", "<h1>"),
                   (parser.meta.get("twitter:title", ""), "twitter:title"), (parser.title, "<title>")]
    for text, source in candidates:
        if " ".join(text.split()):
            facts.headline, facts.headline_source = htmllib.unescape(" ".join(text.split())), source
            break
    facts.h1_count = len(parser.h1)

    # Original publisher of a syndicated copy.
    facts.original_publisher = (
        parser.meta.get("original-source", "") or parser.meta.get("syndication-source", "")
        or next((str(n.get("sourceOrganization", {}).get("name", "")) for n in ld_nodes
                 if isinstance(n.get("sourceOrganization"), dict)), "")
    )
    if not facts.original_publisher and parser.canonical:
        facts.original_publisher = urlparse(parser.canonical).netloc.removeprefix("www.")

    # Body text: paragraphs inside <article>/<main> when there are any, else every paragraph.
    scoped = [t for t, inside in parser.paragraphs if inside]
    paragraphs = scoped if sum(len(t.split()) for t in scoped) >= 60 else [t for t, _ in parser.paragraphs]
    paragraphs = [t for t in paragraphs if len(t.split()) >= 5]
    if sum(len(t.split()) for t in paragraphs) < 60:
        loose = [t for t in parser.text if len(t.split()) >= 8]
        if sum(len(t.split()) for t in loose) > sum(len(t.split()) for t in paragraphs):
            paragraphs = list(dict.fromkeys(loose))
    facts.body_text = "\n".join(paragraphs)[:BODY_CHARS * 4]
    facts.word_count = sum(len(t.split()) for t in paragraphs)
    facts.links = parser.links
    return facts


def strip_site_suffix(headline: str, *names: str) -> str:
    """Remove ' | Site' or ' - Site' tails: a tail naming the site/source, or any short '|' tail."""
    text = " ".join(str(headline or "").split())
    known = [re.sub(r"[^a-z0-9]", "", n.casefold()) for n in names if n]
    for _ in range(2):
        match = re.search(r"\s+(\||[-–—])\s+([^|–—]{2,60})$", text)
        if not match:
            break
        tail = match.group(2).strip()
        flat = re.sub(r"[^a-z0-9]", "", tail.casefold())
        names_site = bool(flat) and any(flat in k or (k and k in flat) for k in known if k)
        if names_site or (match.group(1) == "|" and len(tail.split()) <= 5):
            text = text[: match.start()].rstrip()
        else:
            break
    return text


def headline_problems(headline: str, *, publication_names: list[str] | None = None) -> list[str]:
    """Reasons this is not a usable headline (empty list means it is fine)."""
    config = load_config()
    text = " ".join(str(headline or "").split())
    problems: list[str] = []
    if len(text.split()) < 3:
        problems.append("too short to be a headline")
    flat = re.sub(r"[^a-z0-9]", "", text.casefold())
    for name in publication_names or []:
        if flat and flat == re.sub(r"[^a-z0-9]", "", name.casefold()):
            problems.append("is the publication name")
    pattern = compiled(config["bad_headline_patterns"])
    found = pattern.search(text) if pattern else None
    if found:
        problems.append(f"looks like a page label or slug ({found.group(0).strip()!r})")
    words = re.findall(r"[A-Za-z0-9']+", text)
    cut_offs = config["cut_off_last_words"]
    if words and (words[-1] in cut_offs or (len(words[-1]) > 2 and words[-1].casefold() in {w.casefold() for w in cut_offs})):
        problems.append("ends on a cut-off word")
    if re.search(r"\b\w+(?:-\w+)*-\d{5,}\b", text):
        problems.append("contains a numeric ID")
    return problems


def looks_non_english(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    if sum(ord(c) > 0x024F for c in letters) / len(letters) > 0.3:
        return True
    words = re.findall(r"[A-Za-zÀ-ÿ']+", text.casefold())
    english = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "is", "are", "as", "at", "by", "with", "from",
               "new", "after", "over", "says", "will", "its", "his", "her", "their", "us", "u.s.", "faa", "drone",
               "drones", "rail", "uas", "vehicle", "vehicles", "air", "rule", "plan"}
    foreign = {"le", "la", "les", "des", "du", "un", "une", "et", "der", "die", "das", "und", "el", "los", "las", "del",
               "para", "con", "por", "il", "di", "che", "het", "een", "van", "och", "att", "på", "är", "sur", "dans"}
    return len(words) >= 3 and sum(w in foreign for w in words) >= 2 and sum(w in english for w in words) <= 1


def window_cutoff(now: datetime) -> datetime:
    config = load_config()["freshness"]
    hours = freshness_hours(now, normal=int(config["normal_hours"]), extended=int(config["extended_hours"]))
    return now.astimezone(EASTERN) - timedelta(hours=hours)


def url_rejection(url: str) -> str:
    """Why a URL can never be an article, or ''."""
    config = load_config()
    parsed = urlparse(url.strip())
    host = parsed.netloc.casefold().removeprefix("www.")
    path = parsed.path
    for denied in config["deny_hosts"]:
        denied_host, _, denied_path = denied.partition("/")
        if (host == denied_host or host.endswith("." + denied_host)) and (not denied_path or path.casefold().startswith("/" + denied_path)):
            return f"denied host ({denied})"
    if not path.strip("/") and not parsed.query:
        return "home page"
    for pattern in config["non_article_path_patterns"]:
        match = re.search(pattern, path.rstrip("/") + "/", re.I)
        if match:
            return f"non-article URL pattern ({match.group(0).strip('/')})"
    return ""


_HREF = re.compile(r"""href=["'](https?://[^"'#\s<>]+)""", re.I)
_GOV_BOILERPLATE = re.compile(
    r"/(?:about(?:-us)?(?:[/?.]|$)|(?:privacy|accessibility|foia|disclaimer|contact|subscribe|careers|jobs|sitemap|search|plugins?|"
    r"vulnerability|no-?fear|site-?policies)(?:[/?._-]|$))", re.I)  # not /about-fra/...: FRA's releases live there


def is_official_host(url: str) -> bool:
    host = urlparse(url).netloc.casefold().split(":")[0]
    return host.endswith((".gov", ".mil"))


def official_links(page_html: str, limit: int = 8) -> list[str]:
    """U.S. government pages an article links to (the release, order or rule it reports), in page order."""
    found: list[str] = []
    for match in _HREF.finditer(page_html or ""):
        url = htmllib.unescape(match.group(1)).rstrip(").,;")
        host = urlparse(url).netloc.casefold().removeprefix("www.")
        if (not is_official_host(url) or host == "usa.gov" or _GOV_BOILERPLATE.search(urlparse(url).path)
                or url_rejection(url) or url in found):
            continue
        found.append(url)
        if len(found) >= limit:
            break
    return found


def make_official_fetch(session: requests.Session | None = None) -> Callable[[str], dict[str, str] | None]:
    """fetch(url) -> {"title", "text"} for a live (HTTP 200) U.S. government article page, else None."""
    http = session or requests.Session()
    timeout = float(load_config()["freshness"]["fetch_timeout_seconds"])
    seen: dict[str, dict[str, str] | None] = {}

    def fetch(url: str) -> dict[str, str] | None:
        if url not in seen:
            seen[url] = None
            if is_official_host(url) and not url_rejection(url):
                status, body, final_url = fetch_page(url, http, timeout)
                facts = extract_page(body, final_url or url) if status == 200 and body else None
                if facts and facts.word_count >= 60 and facts.headline:
                    seen[url] = {"title": facts.headline, "text": facts.body_text[:3000]}
        return seen[url]

    return fetch


_GOV_INDEX = re.compile(r"/(?:news|newsroom|press-releases?|releases|briefing-room|media|blogs?|presidential-actions)/?$", re.I)


def make_official_search(base_url: str, session: requests.Session | None = None) -> Callable[[str, str], list[dict[str, str]]]:
    """search(headline, site) -> past-week government article results [{"url", "title", "text"}] from SearXNG."""
    http = session or requests.Session()

    def search(headline: str, site: str) -> list[dict[str, str]]:
        words = re.sub(r"[^\w$.%-]+", " ", headline).split()[:14]
        response = http.get(
            base_url.rstrip("/") + "/search",
            params={"q": " ".join(words) + f" site:{site}", "format": "json", "language": "en-US", "time_range": "week"},
            timeout=30,
        )
        response.raise_for_status()
        found = []
        for result in response.json().get("results", []):
            url = str(result.get("url", ""))
            path = urlparse(url).path
            if (not is_official_host(url) or url_rejection(url) or _GOV_BOILERPLATE.search(path) or _GOV_INDEX.search(path)
                    or len([p for p in path.split("/") if p]) < 2 or url.casefold().endswith(".pdf")):
                continue
            found.append({"url": url, "title": " ".join(str(result.get("title", "")).split()),
                          "text": " ".join(str(result.get("content", "")).split())})
        return found

    return search


def resolve_listing_to_article(page_html: str, base_url: str, title: str) -> str:
    """For a hub/author/section page: the link whose anchor text best matches the wanted headline."""
    wanted = {w for w in re.findall(r"[a-z0-9]{4,}", title.casefold())}
    if len(wanted) < 3:
        return ""
    best, best_score = "", 0.0
    for href, text in extract_page(page_html, base_url).links:
        target = urljoin(base_url, href)
        if url_rejection(target) or urlparse(target).netloc.removeprefix("www.") != urlparse(base_url).netloc.removeprefix("www."):
            continue
        have = {w for w in re.findall(r"[a-z0-9]{4,}", text.casefold())}
        score = len(wanted & have) / len(wanted)
        if score > best_score:
            best, best_score = target, score
    return best if best_score >= 0.6 else ""


@dataclass
class Verdict:
    keep: bool
    reason: str = ""
    record: dict[str, Any] = field(default_factory=dict)


def fetch_page(url: str, session: requests.Session, timeout: float) -> tuple[int | None, str, str]:
    try:
        response = session.get(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
                               timeout=timeout, allow_redirects=True)
    except requests.RequestException:
        return None, "", url
    ctype = response.headers.get("content-type", "").lower()
    if "html" not in ctype and "xml" not in ctype and ctype:
        return response.status_code, "", response.url
    return response.status_code, response.text, response.url


def prune_old_fetches(parent: Path, *, keep_days: int, today: date) -> None:
    """Saved HTML is for reproducing recent problems; folders named by date older than ``keep_days`` are removed."""
    import shutil

    try:
        for folder in parent.iterdir():
            try:
                age = (today - date.fromisoformat(folder.name)).days
            except ValueError:
                continue
            if folder.is_dir() and age > keep_days:
                shutil.rmtree(folder, ignore_errors=True)
    except OSError:
        pass


_INDEX_LOCK = threading.Lock()


def save_fetched(directory: Path, url: str, body: str) -> None:
    """Keep the exact HTML of every fetched page so a problem can be reproduced offline."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        (directory / f"{name}.html.gz").write_bytes(gzip.compress(body.encode("utf-8", "replace")))
        with _INDEX_LOCK:  # fetches run in threads; the index file is shared
            index = directory / "index.json"
            try:
                known = json.loads(index.read_text(encoding="utf-8")) if index.exists() else {}
            except ValueError:
                known = {}
            known[name] = url
            index.write_text(json.dumps(known, indent=1), encoding="utf-8")
    except OSError:
        pass


def evaluate_candidate(
    record: dict[str, Any],
    page: tuple[int | None, str, str] | None,
    *,
    now: datetime,
    translate: Callable[[str], str] | None = None,
    follow: Callable[[str], tuple[int | None, str, str]] | None = None,
) -> Verdict:
    """Apply the date, article-ness and headline rules to one candidate and its fetched page."""
    config = load_config()
    freshness = config["freshness"]
    url = str(record.get("url", ""))
    official = urlparse(url).netloc.removeprefix("www.") in set(freshness["official_hosts"]) or record.get("origin") == "Federal Register API"
    names = [str(record.get("source", "")), urlparse(url).netloc.removeprefix("www.")]
    names += [v for k, v in config["source_names"].items() if urlparse(url).netloc.removeprefix("www.").endswith(k)]

    rejection = url_rejection(url)
    status, body, final_url = page if page else (None, "", url)
    if rejection:
        # One hop: a listing/author page that links to the very story we were sent for.
        target = resolve_listing_to_article(body, final_url or url, str(record.get("title", ""))) if body else ""
        if not target:
            return Verdict(False, f"not an article: {rejection}")
        page = follow(target) if follow else None
        if not page or not page[1]:
            return Verdict(False, f"not an article: {rejection}; the linked story could not be fetched")
        record = {**record, "url": target}
        url, (status, body, final_url) = target, page
        if url_rejection(url):
            return Verdict(False, f"not an article: {url_rejection(url)}")

    facts = extract_page(body, final_url or url) if body else PageFacts()
    published = facts.published
    if official and record.get("origin") == "Federal Register API":
        published, facts.date_source = parse_date(str(record.get("published", ""))), "Federal Register publication date"
    if published is None and not body:
        published = date_from_url(url)
        facts.date_source = "date in URL" if published else ""
    if published is None:
        why = "page could not be fetched" if not body else "no publication date found in the article"
        return Verdict(False, f"no verifiable publication date ({why}; HTTP {status})")
    if published > now + MAX_FUTURE:
        return Verdict(False, f"publication date {published.date()} is in the future")
    cutoff = window_cutoff(now)
    if published < cutoff:
        return Verdict(False, f"stale: published {published.date()} ({facts.date_source}), before the {cutoff:%b %d %H:%M} cutoff")

    if not official:
        if not body:
            return Verdict(False, f"page could not be fetched (HTTP {status}), so it cannot be confirmed as an article")
        words_needed = int(freshness["min_body_words"])
        if urlparse(url).netloc.casefold().endswith((".gov", ".mil")):
            words_needed = min(words_needed, 60)  # agency pages and image articles are short but primary
        if facts.word_count < words_needed:
            return Verdict(False, f"not an article: only {facts.word_count} words of body text (need {words_needed})")
        if facts.h1_count > 1 and not facts.is_article_type:
            return Verdict(False, f"not an article: {facts.h1_count} headlines on the page")

    wanted_names = [facts.site_name, facts.original_publisher, *names]
    headline = strip_site_suffix(facts.headline, *wanted_names) if facts.headline else ""
    fallback = strip_site_suffix(str(record.get("title", "")), *wanted_names)

    def prepare(text: str) -> tuple[str, list[str], str]:
        """(English headline, problems, original-language headline). Foreign headlines are translated first."""
        original = ""
        if text and looks_non_english(text):
            if translate is None:
                return text, ["non-English headline and no translator available"], ""
            english = " ".join(str(translate(text) or "").split())
            if not english or looks_non_english(english):
                return text, ["non-English headline could not be translated cleanly"], ""
            original, text = text, english
        return text, (headline_problems(text, publication_names=wanted_names) if text else ["no headline"]), original

    chosen, problems, original = prepare(headline)
    if problems and fallback and fallback != headline:
        alt, alt_problems, alt_original = prepare(fallback)
        if not alt_problems:
            chosen, problems, original = alt, [], alt_original
    if problems:
        return Verdict(False, f"bad headline {chosen!r}: {'; '.join(problems)}")
    if original:
        record = {**record, "title_original_language": original}

    enriched = {
        **record,
        "title": chosen,
        "published": published.isoformat(),
        "date_label": published.strftime("%b. %d, %Y").replace(" 0", " "),
        "date_source": facts.date_source,
        "headline_source": facts.headline_source or "feed title",
        "page_text": facts.body_text[:BODY_CHARS],
        "page_words": facts.word_count,
        "page_site_name": facts.site_name,
        "page_original_publisher": facts.original_publisher,
        "page_official_links": official_links(body),
    }
    return Verdict(True, "ok", enriched)


def enrich_candidates(
    articles: list[dict[str, Any]],
    *,
    now: datetime,
    fetched_dir: Path | None = None,
    session: requests.Session | None = None,
    fetch: Callable[[str], tuple[int | None, str, str]] | None = None,
    translate: Callable[[str], str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Stage between URL resolution and analysis. Returns (kept records, dropped items with reasons).

    Editor-vetted (required) records are enriched when possible but never dropped.
    """
    config = load_config()["freshness"]
    http = session or requests.Session()
    timeout = float(config["fetch_timeout_seconds"])
    if fetched_dir is not None:
        prune_old_fetches(fetched_dir.parent, keep_days=14, today=now.date())

    def get(url: str) -> tuple[int | None, str, str]:
        if fetch is not None:
            return fetch(url)
        result = fetch_page(url, http, timeout)
        if fetched_dir is not None and result[1]:
            save_fetched(fetched_dir, url, result[1])
        return result

    def work(record: dict[str, Any]) -> Verdict:
        url = str(record.get("url", ""))
        official_api = record.get("origin") == "Federal Register API"
        try:
            page = None if official_api else get(url) if url.startswith(("http://", "https://")) else None
            return evaluate_candidate(record, page, now=now, translate=translate, follow=get)
        except Exception as exc:  # one odd page must not stop the run
            return Verdict(False, f"could not be evaluated ({type(exc).__name__})")

    with ThreadPoolExecutor(max_workers=int(config["max_workers"])) as pool:
        verdicts = list(pool.map(work, articles))
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, str]] = []
    for record, verdict in zip(articles, verdicts):
        if verdict.keep:
            kept.append(verdict.record)
        elif record.get("required_include"):
            kept.append(record)
        else:
            dropped.append({"stage": "article-check", "title": str(record.get("title", ""))[:140],
                            "url": str(record.get("url", "")), "reason": verdict.reason})
    return kept, dropped
