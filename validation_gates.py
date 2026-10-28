"""Deterministic publication gates (rebuild plan Section 7.3).

Every gate returns a ``GateResult``; ``run_gates`` collects them into a
validation report. Gates never call a model and never "repair" content: a
failing gate blocks publication. Do not weaken, skip or disable a gate to make
a run pass (AGENTS.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Callable, Iterable
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

# Text produced by deterministic fallbacks. None may ever appear in an edition.
TEMPLATE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bsource headline\s*:",
        r"watch for follow-?up developments",
        r"today'?s briefing covers the most consequential",
        r"^key developments include\b",
        r"^the leading development is\b",
        r"imported from the supplemental",
        r"\badditional headlines\b",
        r"\bno (?:significant |major )?(?:news|developments) (?:was|were) found\b",
    )
)

# Link hosts that wrap another article instead of being a publisher/official URL.
AGGREGATOR_HOST_ONLY = ("news.google.com", "feedproxy.google.com", "r.search.yahoo.com")

OFFICIAL_SUFFIXES = (".gov", ".mil")
OFFICIAL_HOSTS = ("federalregister.gov", "regulations.gov", "reginfo.gov")

REQUIRED_RINS = (
    "2120-AL82",
    "1652-AA80",
    "2120-AL33",
    "2120-AM15",
    "2127-AM70",
    "2127-AM71",
    "2127-AM72",
    "2127-AN00",
    "2127-AM57",
    "2126-AC17",
)

# Proceedings tracked by docket rather than RIN; they must be in every edition too.
REQUIRED_KEYS = ("fcc-space-modernization",)

STATUS_CLASSES = (
    "completed operation",
    "regulatory action",
    "award",
    "test or demonstration",
    "announced plan",
    "proposal",
    "research",
    "opinion",
)

STOPWORDS = frozenset(
    "a an the and or of to in on for with by at from as is are was were be been it its this that "
    "these those into over under after before about than then".split()
)

MIN_STORIES_WHEN_CANDIDATES = 15
ANTI_BOT_STATUSES = frozenset({401, 402, 403, 429, 451, 999})  # page exists; publisher refuses bots or paywalls
HEADLINE_ECHO_THRESHOLD = 0.80
NEAR_DUPLICATE_TITLE_RATIO = 0.90
SAME_EVENT_CONTAINMENT = 0.75
SAME_EVENT_MIN_SHARED = 5
TRACKER_MAX_AGE = timedelta(hours=24)


@dataclass
class GateResult:
    check: str
    passed: bool
    evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "passed": self.passed,
            "evidence": self.evidence[:25],
            "warnings": self.warnings[:25],
        }


def iter_stories(briefing: dict[str, Any]) -> Iterable[tuple[str, int, dict[str, Any]]]:
    sections = briefing.get("sections")
    if not isinstance(sections, dict):
        return
    for section, items in sections.items():
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items, start=1):
            if isinstance(item, dict):
                yield section, index, item


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", text.casefold())
        if token not in STOPWORDS
    ]


def canonical_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = parsed.netloc.casefold().removeprefix("www.")
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query)
            if not key.casefold().startswith("utm_")
            and key.casefold() not in {"fbclid", "gclid", "ref", "cmpid"}
        )
    )
    path = parsed.path.rstrip("/")
    return f"{host}{path}" + (f"?{query}" if query else "")


def url_host(url: str) -> str:
    return urlparse(url.strip()).netloc.casefold().removeprefix("www.")


def is_aggregator_url(url: str) -> bool:
    host = url_host(url)
    if host in AGGREGATOR_HOST_ONLY:
        return True
    parsed = urlparse(url.strip())
    if host == "google.com" and parsed.path.startswith(("/url", "/amp")):
        return True
    return host.endswith("bing.com") and "apiclick" in parsed.path.casefold()


def is_official_url(url: str) -> bool:
    host = url_host(url)
    return host.endswith(OFFICIAL_SUFFIXES) or any(
        host == name or host.endswith("." + name) for name in OFFICIAL_HOSTS
    )


def edition_date(briefing: dict[str, Any]) -> date | None:
    for key in ("briefing_date", "edition_date"):
        value = briefing.get(key)
        if value:
            try:
                return date.fromisoformat(str(value)[:10])
            except ValueError:
                return None
    try:
        return datetime.fromisoformat(str(briefing["window_end"])).astimezone(EASTERN).date()
    except (KeyError, ValueError, TypeError):
        return None


def _text_fields(briefing: dict[str, Any]) -> Iterable[tuple[str, str]]:
    yield "executive_summary", str(briefing.get("executive_summary", ""))
    for index, item in enumerate(briefing.get("what_to_watch") or [], start=1):
        yield f"what_to_watch[{index}]", str(item.get("text", item) if isinstance(item, dict) else item)
    for section, index, story in iter_stories(briefing):
        yield f"{section}[{index}].summary", str(story.get("summary", ""))
        yield f"{section}[{index}].win_explanation", str(story.get("win_explanation", ""))


def gate_freshness(
    briefing: dict[str, Any],
    *,
    today: date | None = None,
    previous_cutoff: datetime | None = None,
) -> GateResult:
    """Edition date is ``today`` (ET) when given, and the window starts at the prior cutoff."""
    evidence: list[str] = []
    day = edition_date(briefing)
    if day is None:
        evidence.append("Edition has no parseable briefing date or window_end.")
    else:
        try:
            window_end = datetime.fromisoformat(str(briefing["window_end"])).astimezone(EASTERN)
            if window_end.date() != day:
                evidence.append(f"window_end date {window_end.date()} differs from edition date {day}.")
        except (KeyError, ValueError, TypeError):
            evidence.append("Edition has no valid window_end.")
        if today is not None and day != today:
            evidence.append(f"Edition date {day} is not today ({today}) in ET.")
    if previous_cutoff is not None:
        try:
            start = datetime.fromisoformat(str(briefing["window_start"])).astimezone(EASTERN)
            if start > previous_cutoff.astimezone(EASTERN) + timedelta(minutes=1):
                evidence.append(
                    f"Window starts {start.isoformat()}, after the previous edition cutoff "
                    f"{previous_cutoff.isoformat()}; stories could be missed."
                )
        except (KeyError, ValueError, TypeError):
            evidence.append("Edition has no valid window_start.")
    return GateResult("freshness", not evidence, evidence)


def gate_template_text(briefing: dict[str, Any]) -> GateResult:
    evidence = [
        f"{where}: matches {pattern.pattern!r}"
        for where, text in _text_fields(briefing)
        for pattern in TEMPLATE_PATTERNS
        if text and pattern.search(text.strip())
    ]
    return GateResult("template_text", not evidence, evidence)


def headline_echo_ratio(title: str, summary: str) -> float:
    """Share of the summary's content words that already appear in the headline."""
    summary_tokens = _tokens(summary)
    if not summary_tokens:
        return 0.0
    title_tokens = set(_tokens(title))
    return sum(token in title_tokens for token in summary_tokens) / len(summary_tokens)


def gate_headline_echo(briefing: dict[str, Any]) -> GateResult:
    evidence = []
    for section, index, story in iter_stories(briefing):
        title = str(story.get("title", ""))
        summary = str(story.get("summary", ""))
        if not summary.strip():
            continue
        ratio = headline_echo_ratio(title, summary)
        if ratio > HEADLINE_ECHO_THRESHOLD:
            evidence.append(f"{section}[{index}] summary repeats its headline ({ratio:.0%} overlap): {title[:80]}")
    return GateResult("headline_echo", not evidence, evidence)


def titles_are_near_identical(first: str, second: str) -> bool:
    """Same wording, or the shorter headline's content words sit almost wholly in the other."""
    a, b = _tokens(first), _tokens(second)
    if not a or not b:
        return False
    if SequenceMatcher(None, " ".join(a), " ".join(b)).ratio() >= NEAR_DUPLICATE_TITLE_RATIO:
        return True
    shared = set(a) & set(b)
    smaller = min(len(set(a)), len(set(b)))
    return len(shared) >= SAME_EVENT_MIN_SHARED and len(shared) / smaller >= SAME_EVENT_CONTAINMENT


def gate_duplicates(briefing: dict[str, Any]) -> GateResult:
    evidence: list[str] = []
    seen_urls: dict[str, str] = {}
    seen_clusters: dict[str, str] = {}
    titles: list[tuple[str, str]] = []  # (title, label)
    for section, index, story in iter_stories(briefing):
        label = f"{section}[{index}]"
        url = str(story.get("url", "")).strip()
        if url:
            key = canonical_url(url)
            if key in seen_urls:
                evidence.append(f"{label} repeats the URL of {seen_urls[key]}: {key}")
            else:
                seen_urls[key] = label
        cluster = str(story.get("cluster_id", "")).strip()
        if cluster:
            if cluster in seen_clusters:
                evidence.append(f"{label} shares cluster {cluster} with {seen_clusters[cluster]}")
            else:
                seen_clusters[cluster] = label
        title = str(story.get("title", ""))
        for other_title, other_label in titles:
            if titles_are_near_identical(title, other_title):
                evidence.append(f"{label} has a near-identical title to {other_label}: {story.get('title', '')[:80]}")
                break
        titles.append((title, label))
    return GateResult("duplicates", not evidence, evidence)


def gate_relevance(briefing: dict[str, Any], *, require_status_class: bool = False) -> GateResult:
    """Every story has a category; the status class becomes mandatory in Phase 2."""
    evidence = []
    for section, index, story in iter_stories(briefing):
        if not str(section).strip():
            evidence.append(f"story {index} has no category")
        status = str(story.get("status_class", "")).strip()
        if require_status_class and not status:
            evidence.append(f"{section}[{index}] has no status class")
        elif status and status not in STATUS_CLASSES:
            evidence.append(f"{section}[{index}] has unknown status class {status!r}")
    return GateResult("relevance", not evidence, evidence)


def gate_links(
    briefing: dict[str, Any],
    *,
    fetch_status: Callable[[str], int | None] | None = None,
) -> GateResult:
    """No aggregator wrappers; optionally require live HTTP 200 (``fetch_status``)."""
    evidence: list[str] = []
    urls: list[tuple[str, str]] = []
    for section, index, story in iter_stories(briefing):
        label = f"{section}[{index}]"
        urls.append((label, str(story.get("url", "")).strip()))
        for extra in story.get("also_covered") or []:
            extra_url = str(extra.get("url", "") if isinstance(extra, dict) else extra).strip()
            if extra_url:
                urls.append((f"{label}.also_covered", extra_url))
    for label, url in urls:
        if not url.startswith(("http://", "https://")):
            evidence.append(f"{label} has no public http(s) URL")
        elif is_aggregator_url(url):
            evidence.append(f"{label} uses an aggregator redirect wrapper: {url[:90]}")
    warnings: list[str] = []
    if fetch_status is not None:
        for label, url in urls:
            if url.startswith(("http://", "https://")) and not is_aggregator_url(url):
                status = fetch_status(url)
                if status == 200:
                    continue
                if status in ANTI_BOT_STATUSES:
                    # The page exists but the publisher refuses automated requests.
                    warnings.append(f"{label} blocked automated check (HTTP {status}): {url[:90]}")
                else:
                    evidence.append(f"{label} returned HTTP {status}: {url[:90]}")
    return GateResult("links", not evidence, evidence, warnings)


def check_url_status(url: str, *, timeout: float = 15.0) -> int | None:
    """Live HTTP status after redirects, or None when the host is unreachable.

    One retry for network errors and 5xx so a transient blip does not condemn a link.
    """
    import requests

    headers = {"User-Agent": "Mozilla/5.0 (compatible; AdvancedTransportationNewsBot/1.0)"}
    status: int | None = None
    for attempt in range(2):
        try:
            response = requests.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=True)
            status = response.status_code
            response.close()
        except requests.RequestException:
            status = None
        if status is not None and status < 500:
            return status
    return status


NON_ARTICLE_HOSTS = frozenset(
    {"reddit.com", "old.reddit.com", "facebook.com", "x.com", "twitter.com", "linkedin.com",
     "youtube.com", "youtu.be", "instagram.com", "tiktok.com", "wikipedia.org", "en.wikipedia.org",
     "finance.yahoo.com", "sg.finance.yahoo.com", "quote.cnbc.com"}
)
NON_ARTICLE_PATH = re.compile(r"/(?:topics?|tags?|category|categories|search|quote|hashtag|document-library|library)(?:/|$)", re.IGNORECASE)
# A URL whose last path segment is one of these is a section/index page, not a story.
INDEX_SEGMENTS = frozenset(
    {"news", "newsroom", "press", "press-releases", "press_releases", "press-release", "press_release",
     "media", "media-center", "media-room", "articles", "blog", "updates", "latest", "latest-news",
     "headlines", "releases", "announcements", "home", "index", "index.html", "index.htm", "feed", "rss"}
)
# Titles that name a listing page or an SEO roundup instead of reporting an event.
NON_ARTICLE_TITLE = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^(?:press releases?|newsroom|latest news|headlines|media (?:center|room)|announcements)\b",
        r"\bpress releases?\b(?:\s+and\s+announcements)?\s*[-|–—]",
        r"\bnews\s+20\d\d\b",
        r"\[20\d\d\]",
        r"^(?:the\s+)?(?:top|best)\s+\d+\b",
        r",\s*ranked$",
        r"^tracking every\b",
        r"[-|]\s*(?:reddit|youtube|facebook|linkedin|x \(formerly twitter\))\s*$",
    )
)


def is_non_article_url(url: str) -> bool:
    """Social posts, quote pages, topic/index pages, year archives and home pages are not stories."""
    parsed = urlparse(url.strip())
    host = parsed.netloc.casefold().removeprefix("www.")
    if host in NON_ARTICLE_HOSTS or host.endswith(".reddit.com"):
        return True
    path = parsed.path.rstrip("/")
    if not path or NON_ARTICLE_PATH.search(path + "/"):
        return True
    segments = [segment for segment in path.split("/") if segment]
    last = segments[-1].casefold()
    if last in INDEX_SEGMENTS:
        return True
    # Year or year/month archive pages (".../news/army-news/2026", ".../2026/10").
    if re.fullmatch(r"(?:19|20)\d\d", last) or (
        len(segments) >= 2 and re.fullmatch(r"(?:19|20)\d\d", segments[-2]) and re.fullmatch(r"\d{1,2}", last)
    ):
        return True
    # One short, digit-free segment (".../futureflight") is a section front, not a slug.
    # A query string with an id (".../index.cfm?id=76") marks an individual article.
    if (
        len(segments) == 1
        and not re.search(r"\d", parsed.query)
        and not re.search(r"\d", last)
        and len(re.split(r"[-_]", last)) < 2
    ):
        return True
    return False


def is_non_article_title(title: str) -> bool:
    text = " ".join(str(title or "").split())
    return any(pattern.search(text) for pattern in NON_ARTICLE_TITLE)


def is_non_article(title: str, url: str) -> bool:
    return is_non_article_title(title) or is_non_article_url(url)


def drop_non_articles(
    articles: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Remove listing pages, social posts and SEO roundups from the candidate pool.

    Editor-vetted (required) records are never dropped. Returns (kept, removed records).
    """
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, str]] = []
    for article in articles:
        if not article.get("required_include") and is_non_article(
            str(article.get("title", "")), str(article.get("url", ""))
        ):
            removed.append({"title": str(article.get("title", ""))[:120], "url": str(article.get("url", ""))})
        else:
            kept.append(article)
    return kept, removed


def prune_unusable_stories(
    briefing: dict[str, Any],
    fetch_status: Callable[[str], int | None],
) -> list[dict[str, str]]:
    """Deterministic pre-validation cleanup; every removal is recorded, none is silent.

    - A story whose link is dead (404/410/5xx/unreachable) is removed.
    - A dead "also covered" link is dropped from its story (the story itself stays).
    - A summary that restates its headline is blanked (the headline stands alone).
    The hard gates then re-verify what remains.
    """
    from concurrent.futures import ThreadPoolExecutor

    removed: list[dict[str, str]] = []
    sections = briefing.get("sections")
    if not isinstance(sections, dict):
        return removed
    def extra_url(extra: Any) -> str:
        return str(extra.get("url", "") if isinstance(extra, dict) else extra).strip()

    urls = sorted(
        {
            str(story.get("url", "")).strip()
            for items in sections.values()
            for story in items
            if str(story.get("url", "")).startswith(("http://", "https://"))
            and not is_aggregator_url(str(story.get("url", "")))
            and not is_non_article(str(story.get("title", "")), str(story.get("url", "")))
        }
        | {
            extra_url(extra)
            for items in sections.values()
            for story in items
            for extra in story.get("also_covered") or []
            if extra_url(extra).startswith(("http://", "https://")) and not is_aggregator_url(extra_url(extra))
        }
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        cache: dict[str, int | None] = dict(zip(urls, pool.map(fetch_status, urls)))
    for section, items in sections.items():
        kept = []
        for story in items:
            url = str(story.get("url", "")).strip()
            if story.get("required_include") is not True and url.startswith(("http://", "https://")) and is_non_article(str(story.get("title", "")), url):
                removed.append({"section": section, "title": str(story.get("title", ""))[:120],
                                "url": url, "reason": "not a news article (listing, social, quote, topic, home or roundup page)"})
                continue
            if url.startswith(("http://", "https://")) and not is_aggregator_url(url):
                status = cache[url]
                if status is None or status in (404, 410) or status >= 500:
                    removed.append({"section": section, "title": str(story.get("title", ""))[:120],
                                    "url": url, "reason": f"dead link (HTTP {status})"})
                    continue
            live_extras = []
            for extra in story.get("also_covered") or []:
                target = extra_url(extra)
                status = cache.get(target)
                if target in cache and (status is None or status in (404, 410) or status >= 500):
                    removed.append({"section": section, "title": str(story.get("title", ""))[:120],
                                    "url": target, "reason": f"dead also-covered link dropped (HTTP {status})"})
                else:
                    live_extras.append(extra)
            if "also_covered" in story:
                story["also_covered"] = live_extras
            summary = str(story.get("summary", ""))
            if summary.strip() and headline_echo_ratio(str(story.get("title", "")), summary) > HEADLINE_ECHO_THRESHOLD:
                story["summary"] = ""
                removed.append({"section": section, "title": str(story.get("title", ""))[:120],
                                "url": url, "reason": "summary restated headline; blanked"})
            kept.append(story)
        sections[section] = kept
    return removed


_EVIDENCE_LABEL = re.compile(r"^(?P<section>.+?)\[(?P<index>\d+)\](?P<field>\.[a-z_]+)?")


def repair_story_failures(
    briefing: dict[str, Any],
    report: dict[str, Any],
    fetch_status: Callable[[str], int | None] | None = None,
) -> list[dict[str, str]]:
    """Fix failures that belong to one story so one bad item cannot sink the whole edition.

    Only item-level evidence is repaired (headline echo, template text in a story field, duplicate
    stories, unusable story or also-covered links, unknown status class). Anything about the edition
    itself (freshness, summary, tracker, provenance, schema) is left alone and still fails. The gates
    run again on whatever remains, so this never weakens a gate; every change is returned as a record.
    """
    sections = briefing.get("sections")
    if not isinstance(sections, dict):
        return []
    drop: dict[tuple[str, int], str] = {}
    blank: dict[tuple[str, int, str], str] = {}
    extras: set[tuple[str, int]] = set()
    for check in report.get("checks", []):
        if check.get("passed"):
            continue
        name = check.get("check")
        for evidence in check.get("evidence", []):
            match = _EVIDENCE_LABEL.match(str(evidence))
            if not match or match["section"] not in sections:
                continue
            key = (match["section"], int(match["index"]))
            field = (match["field"] or "").lstrip(".")
            if name == "headline_echo":
                blank[(*key, "summary")] = "summary restated headline; blanked"
            elif name == "template_text" and field in ("summary", "win_explanation"):
                blank[(*key, field)] = f"template text in {field}; blanked"
            elif name == "duplicates":
                drop[key] = "duplicate of an earlier story"
            elif name == "links":
                if field == "also_covered":
                    extras.add(key)
                else:
                    drop[key] = "unusable story link"
            elif name == "relevance":
                drop[key] = "unusable category or status"
    removed: list[dict[str, str]] = []

    def story_at(key: tuple[str, int]) -> dict[str, Any] | None:
        items = sections.get(key[0])
        return items[key[1] - 1] if isinstance(items, list) and 0 < key[1] <= len(items) and isinstance(items[key[1] - 1], dict) else None

    def record(story: dict[str, Any], section: str, reason: str, url: str = "") -> None:
        removed.append({"section": section, "title": str(story.get("title", ""))[:120],
                        "url": url or str(story.get("url", "")), "reason": f"repaired: {reason}"})

    for (section, index, field), reason in blank.items():
        story = story_at((section, index))
        if story and (section, index) not in drop and str(story.get(field, "")).strip():
            story[field] = ""
            record(story, section, reason)
    for key in sorted(extras - set(drop)):
        story = story_at(key)
        if not story:
            continue
        kept = []
        for extra in story.get("also_covered") or []:
            url = str(extra.get("url", "") if isinstance(extra, dict) else extra).strip()
            status = fetch_status(url) if fetch_status and url.startswith(("http://", "https://")) else None
            bad = (not url.startswith(("http://", "https://")) or is_aggregator_url(url)
                   or (fetch_status is not None and status != 200 and status not in ANTI_BOT_STATUSES))
            if bad:
                record(story, key[0], "unusable also-covered link dropped", url)
            else:
                kept.append(extra)
        story["also_covered"] = kept
    for key in sorted(drop, key=lambda k: (k[0], -k[1])):
        story = story_at(key)
        if story is not None:
            record(story, key[0], drop[key])
            del sections[key[0]][key[1] - 1]
    return removed


def gate_tracker(
    briefing: dict[str, Any],
    *,
    now: datetime | None = None,
    fetch_status: Callable[[str], int | None] | None = None,
) -> GateResult:
    """All required rulemakings present, official links, recent verification, no unsupported 'final'."""
    evidence: list[str] = []
    warnings: list[str] = []
    now = (now or datetime.now(EASTERN)).astimezone(EASTERN)
    tracker = briefing.get("regulatory_tracker")
    if not isinstance(tracker, list):
        return GateResult("tracker", False, ["The regulatory tracker is missing."])
    by_rin = {str(item.get("rin") or item.get("key") or "").upper(): item for item in tracker if isinstance(item, dict)}
    for rin in (*REQUIRED_RINS, *(k.upper() for k in REQUIRED_KEYS)):
        item = by_rin.get(rin)
        if item is None:
            evidence.append(f"Required rulemaking {rin} is missing.")
            continue
        url = str(item.get("source_url", ""))
        if not url.startswith("https://") or not is_official_url(url):
            evidence.append(f"{rin} has no official source link: {url[:90]}")
        elif fetch_status is not None and fetch_status(url) != 200:
            evidence.append(f"{rin} official link did not return HTTP 200: {url[:90]}")
        verified = str(item.get("verified_at", "")).strip()
        try:
            age = now - datetime.fromisoformat(verified).astimezone(EASTERN)
        except (ValueError, TypeError):
            evidence.append(f"{rin} has never been verified against its official source.")
        else:
            if age > TRACKER_MAX_AGE:
                warnings.append(f"{rin}: verification delayed (last verified {age} ago; limit 24 hours).")
        status = str(item.get("status", "")).casefold()
        if "final rule" in status and "pending" not in status and "stage" not in status:
            if not item.get("final_rule_document"):
                evidence.append(f"{rin} shows a final status without a Federal Register RULE document.")
    return GateResult("tracker", not evidence, evidence, warnings)


def gate_provenance(briefing: dict[str, Any]) -> GateResult:
    evidence: list[str] = []
    provenance = briefing.get("provenance")
    if not isinstance(provenance, dict):
        return GateResult("provenance", False, ["Edition has no provenance record."])
    if provenance.get("fallback_text_used") is not False:
        evidence.append("Edition does not state that no fallback text was used.")
    for field_name in ("executive_summary", "story_analysis"):
        record = provenance.get(field_name)
        if not isinstance(record, dict) or not record.get("model"):
            evidence.append(f"No model recorded for {field_name}.")
    return GateResult("provenance", not evidence, evidence)


SUMMARY_MIN_WORDS, SUMMARY_MAX_WORDS = 140, 350
SUMMARY_BANNED = (
    "landmark", "game-changer", "game changer", "poised to", "underscores", "amid",
    "navigating the landscape", "it remains to be seen", "source headline", "watch for follow-up developments",
)
SUMMARY_PROCESS_WORDS = ("supplemental", "intake", "the feed", "this edition", "this briefing", "today's stories", "the stories below")
_SUMMARY_LINK = re.compile(r"\[([^\]]{1,120})\]\((https?://[^\s)]+)\)")
SUMMARY_CHAIN_VERSION = 2


def gate_summary(briefing: dict[str, Any], *, require_chain: bool = False) -> GateResult:
    """Executive Summary shape, source links and verification (plan 7.3).

    Editions from before the verified chain carry no ``summary_chain`` marker and are validated
    leniently (they are history); every new edition must carry the marker and pass in full.
    """
    marker = briefing.get("summary_chain")
    if marker is None and not require_chain:
        return GateResult("summary", True, [], ["legacy edition: verified summary chain not required"])
    evidence: list[str] = []
    if marker != SUMMARY_CHAIN_VERSION:
        evidence.append("Edition was not produced by the verified summary chain.")
    text = str(briefing.get("executive_summary", ""))
    links = _SUMMARY_LINK.findall(text)
    plain = _SUMMARY_LINK.sub("", text)
    words = len(plain.split())
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    if not SUMMARY_MIN_WORDS <= words <= SUMMARY_MAX_WORDS:
        evidence.append(f"Executive Summary has {words} words (allowed {SUMMARY_MIN_WORDS}-{SUMMARY_MAX_WORDS}).")
    if not 2 <= len(paragraphs) <= 4:
        evidence.append(f"Executive Summary has {len(paragraphs)} paragraphs (allowed 2-4).")
    if len(links) < 3:
        evidence.append(f"Executive Summary has {len(links)} source links (need at least 3).")
    for number, paragraph in enumerate(paragraphs, start=1):
        if not _SUMMARY_LINK.search(paragraph):
            evidence.append(f"Paragraph {number} of the Executive Summary has no source link.")
    story_urls = {str(story.get("url", "")) for _, _, story in iter_stories(briefing)}
    for _, url in links:
        if url not in story_urls:
            evidence.append(f"Executive Summary links to a URL that is not a story in this edition: {url[:80]}")
    lowered = plain.casefold()
    for phrase in SUMMARY_BANNED:
        if re.search(r"\b" + re.escape(phrase) + r"\b", lowered):
            evidence.append(f"Executive Summary uses banned phrase {phrase!r}.")
    for word in SUMMARY_PROCESS_WORDS:
        if word in lowered:
            evidence.append(f"Executive Summary uses process language {word!r}.")
    record = (briefing.get("provenance") or {}).get("executive_summary") or {}
    if not record.get("verified_by"):
        evidence.append("Executive Summary has no recorded verifier model.")
    if record.get("unsupported_remaining") != 0 or record.get("status_errors_remaining") != 0:
        evidence.append("Executive Summary verification left unsupported or status-error sentences.")
    return GateResult("summary", not evidence, evidence)


def gate_schema(briefing: dict[str, Any]) -> GateResult:
    evidence = []
    if not str(briefing.get("executive_summary", "")).strip():
        evidence.append("Executive Summary is empty.")
    if not isinstance(briefing.get("sections"), dict) or not any(
        True for _ in iter_stories(briefing)
    ):
        evidence.append("The edition has no stories.")
    if not briefing.get("what_to_watch"):
        evidence.append("What to Watch is empty.")
    stories = sum(1 for _ in iter_stories(briefing))
    candidates = int(briefing.get("candidate_count", 0) or 0)
    if candidates >= 50 and stories < MIN_STORIES_WHEN_CANDIDATES:
        evidence.append(
            f"Only {stories} stories from {candidates} candidates (minimum {MIN_STORIES_WHEN_CANDIDATES}); "
            "the model output is probably truncated or degraded."
        )
    return GateResult("schema", not evidence, evidence)


def run_gates(
    briefing: dict[str, Any],
    *,
    today: date | None = None,
    now: datetime | None = None,
    previous_cutoff: datetime | None = None,
    fetch_status: Callable[[str], int | None] | None = None,
    require_status_class: bool = False,
    require_summary_chain: bool = False,
) -> dict[str, Any]:
    """Run every gate and return the validation report (never raises for a failed gate)."""
    results = [
        gate_freshness(briefing, today=today, previous_cutoff=previous_cutoff),
        gate_template_text(briefing),
        gate_headline_echo(briefing),
        gate_duplicates(briefing),
        gate_relevance(briefing, require_status_class=require_status_class),
        gate_links(briefing, fetch_status=fetch_status),
        gate_tracker(briefing, now=now, fetch_status=fetch_status),
        gate_provenance(briefing),
        gate_summary(briefing, require_chain=require_summary_chain),
        gate_schema(briefing),
    ]
    return {
        "passed": all(result.passed for result in results),
        "checked_at": (now or datetime.now(EASTERN)).isoformat(),
        "checks": [result.as_dict() for result in results],
    }


def failing_checks(report: dict[str, Any]) -> list[str]:
    return [
        f"{check['check']}: {evidence}"
        for check in report["checks"]
        if not check["passed"]
        for evidence in (check["evidence"] or ["failed"])
    ]
