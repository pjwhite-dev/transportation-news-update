from __future__ import annotations

import hashlib
import html
import json
import random
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import feedparser
import requests

EASTERN = ZoneInfo("America/New_York")
OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
TRANSIENT_OPENAI_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}

OPENAI_TOKEN_PRICES = {
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
    "gpt-5-mini": {"input": 0.25, "output": 2.00},
}

TOPIC_SECTIONS = [
    "UAS and Drones",
    "UAS Security and C-UAS",
    "eVTOL Integration Pilot Program and AAM",
    "Autonomous Vehicles",
    "Other Advanced Transportation",
    "Federal Actions",
]

SECTION_ORDER = [
    "Trump Administration Wins",
    "Top Developments",
    *TOPIC_SECTIONS,
]

NEWS_QUERIES = {
    "Trump Administration priorities": [
        '"Unleashing American Drone Dominance"',
        '"Restoring American Airspace Sovereignty"',
        '"Leading the World in Supersonic Flight"',
        '"eVTOL Integration Pilot Program"',
        '(Trump OR "Trump Administration") (drone OR UAS OR eVTOL OR supersonic OR autonomous vehicle)',
        '(White House OR FAA OR DOT OR OSTP) (drone dominance OR airspace sovereignty OR supersonic)',
    ],
    "UAS and Drones": [
        '("unmanned aircraft system" OR "uncrewed aircraft system" OR UAS) (FAA OR United States OR U.S.)',
        '(drone OR UAS) (BVLOS OR "beyond visual line of sight" OR "Part 107" OR waiver)',
        '(drone OR UAS) ("Remote ID" OR airspace integration OR certification OR rulemaking)',
        '("drone delivery" OR "medical drone delivery" OR "package delivery drone") United States',
        '(drone OR UAS) (inspection OR agriculture OR public safety OR infrastructure) United States',
        '("American drone" OR "U.S. drone manufacturing" OR "domestic drone" OR "trusted drone")',
        '(FAA OR NASA) (drone OR UAS) test range',
    ],
    "UAS Security and C-UAS": [
        '("counter-UAS" OR C-UAS OR "counter drone") United States',
        '("drone detection" OR "drone mitigation") (airport OR stadium OR border OR prison OR infrastructure)',
        '("drone incursion" OR "unauthorized drone" OR "unlawful drone") United States',
        '(drone OR UAS) ("critical infrastructure" OR border security OR military base)',
        '("airspace sovereignty" OR "Section 2209") drone',
        '(DHS OR DOJ OR FAA OR Pentagon OR DoD) ("counter-UAS" OR "counter drone")',
        '("counter-drone" OR "counter UAS") (contract OR deployment OR test OR demonstration)',
    ],
    "eVTOL Integration Pilot Program and AAM": [
        '"eVTOL Integration Pilot Program"',
        '(eIPP AND (FAA OR eVTOL OR aircraft))',
        '("advanced air mobility" OR AAM) (FAA OR United States OR U.S.)',
        '(eVTOL OR "air taxi") (certification OR flight test OR deployment OR operations)',
        '(powered-lift OR vertiport) (FAA OR regulation OR certification)',
        '(Joby OR Archer OR BETA OR Wisk OR Eve OR Lilium) (FAA OR flight OR certification OR U.S.)',
        '(electric aircraft OR eVTOL) (medical logistics OR cargo OR passenger) United States',
    ],
    "Autonomous Vehicles": [
        '("autonomous vehicle" OR "automated vehicle") (NHTSA OR DOT OR United States OR U.S.)',
        '(robotaxi OR "self-driving") (deployment OR expansion OR permit OR regulation) United States',
        '("automated driving system" OR ADS) (NHTSA OR FMVSS OR exemption OR rulemaking)',
        '("autonomous truck" OR "driverless truck") United States',
        '(Waymo OR Cruise OR Zoox OR Tesla OR Aurora OR Motional) (robotaxi OR autonomous vehicle)',
        '("autonomous vehicle" OR robotaxi) (crash OR safety OR investigation OR recall)',
        '(state legislature OR governor OR city) ("autonomous vehicle" OR robotaxi)',
    ],
    "Other Advanced Transportation": [
        '("civil supersonic" OR "commercial supersonic" OR "quiet supersonic") United States',
        '(X-59 OR Boom Supersonic OR Overture OR Hermeus) (flight OR test OR FAA OR NASA)',
        '("overland supersonic" OR "supersonic noise") (FAA OR rule OR regulation)',
        '("high-speed rail" OR bullet train) United States',
        '(maglev OR "autonomous rail" OR "automated train") United States',
        '(FRA OR FTA OR DOT) (rail technology OR advanced rail OR passenger rail)',
        '("advanced transportation technology" OR "smart transportation") United States',
    ],
}

FEDERAL_REGISTER_TERMS = [
    "unmanned aircraft",
    "drone",
    "beyond visual line of sight",
    "remote identification",
    "advanced air mobility",
    "powered-lift",
    "autonomous vehicle",
    "automated driving",
    "motor vehicle safety",
    "supersonic",
    "high-speed rail",
    "passenger rail",
    "transportation technology",
]

HEADERS = {
    "User-Agent": (
        "TransportationNewsUpdate/3.0 "
        "(public-source daily briefing; contact via GitHub repository)"
    )
}

EO_REFERENCE = """
AUTHORITATIVE EXECUTIVE-ORDER REFERENCE

EO 14307 — Unleashing American Drone Dominance (June 6, 2025)
- Sec. 3: American leadership in UAS development, commercialization, exports, routine advanced operations, streamlined approvals, and the trusted American drone industrial base.
- Sec. 4(a): FAA rulemaking to enable routine BVLOS operations.
- Sec. 4(c): AI tools to expedite Part 107 waiver reviews.
- Sec. 5(a): Updated civil-UAS integration roadmap.
- Sec. 5(b): Full use of FAA UAS Test Ranges for BVLOS, autonomy, AAM, testing, scaling, and rulemaking data.
- Sec. 6: Establish eIPP to accelerate safe and lawful eVTOL deployment in the United States and inform regulation and planning.
- Sec. 7: Strengthen the American drone industrial base, prioritize U.S.-manufactured UAS, protect supply chains, and reduce foreign dependence.

EO 14305 — Restoring American Airspace Sovereignty (June 6, 2025)
- Sec. 3: Protect the public, critical infrastructure, mass gatherings, military sites, and sensitive government operations from careless or unlawful UAS use.
- Sec. 4: Federal Task Force to Restore American Airspace Sovereignty.
- Sec. 5: Section 2209 fixed-site restriction rulemaking, security coordination, and open-format NOTAM/TFR information for geofencing and navigation systems.
- Sec. 8: Expanded protections for borders, airports, Federal facilities, critical infrastructure, and military assets.
- Sec. 9: Counter-UAS capacity, operational coordination, training, and Federal/SLTT capabilities.

EO 14304 — Leading the World in Supersonic Flight (June 6, 2025)
- Sec. 2: Remove obsolete barriers to civil supersonic flight and establish noise-based certification standards.
- Sec. 3: OSTP-led coordination of supersonic R&D, testing, regulatory data, commercial viability, and operational integration.
- Sec. 4: International engagement and alignment on civil-supersonic regulation and safety agreements.
""".strip()

EO_DISPLAY_NAMES = {
    "EO 14307": "Unleashing American Drone Dominance",
    "EO 14305": "Restoring American Airspace Sovereignty",
    "EO 14304": "Leading the World in Supersonic Flight",
}

SOURCE_PREFERENCE = """
Prefer, in order: official White House/Federal agency/Federal Register; original program or
company announcement; Reuters/AP/major national outlet; recognized aviation,
transportation, security, or technology trade publication; credible local reporting;
aggregator. Exclude stock promotion, valuation pieces, product lists, celebrity commentary,
generic market reports, and obvious keyword collisions.
""".strip()


def clean_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def strip_html(value: str) -> str:
    return clean_spaces(html.unescape(re.sub(r"<[^>]+>", " ", value or "")))


def normalize_title(value: str) -> str:
    value = value.lower()
    value = re.sub(r"\s+-\s+[^-]{2,60}$", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return clean_spaces(value)


def stable_id(*parts: str) -> str:
    raw = "|".join(str(part or "") for part in parts).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def parse_rss_date(value: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(EASTERN)
    except (TypeError, ValueError, OverflowError):
        return None


def google_news_url(query: str) -> str:
    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(f"{query} when:2d")
        + "&hl=en-US&gl=US&ceid=US:en"
    )


def fetch_google_news(
    search_section: str,
    query: str,
    window_start: datetime,
    window_end: datetime,
    max_items: int = 25,
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        response = requests.get(google_news_url(query), headers=HEADERS, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        return [], f"{search_section}: {exc}"

    feed = feedparser.parse(response.content)
    items: list[dict[str, Any]] = []

    for entry in feed.entries:
        published = parse_rss_date(entry.get("published", ""))
        if published is None or published < window_start or published > window_end:
            continue

        title = clean_spaces(entry.get("title", "Untitled"))
        source_obj = entry.get("source", {})
        source = (
            clean_spaces(source_obj.get("title", ""))
            if isinstance(source_obj, dict)
            else clean_spaces(getattr(source_obj, "title", ""))
        )

        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)].strip()
        elif " - " in title:
            possible_title, possible_source = title.rsplit(" - ", 1)
            if 1 < len(possible_source) < 80:
                title = possible_title.strip()
                source = source or possible_source.strip()

        summary = strip_html(entry.get("summary", ""))
        if len(summary) < 45 or normalize_title(title) in normalize_title(summary):
            summary = ""

        url = entry.get("link", "")
        items.append(
            {
                "id": stable_id(source, title, published.isoformat()),
                "search_section": search_section,
                "title": title,
                "summary": summary,
                "source": source or "Google News",
                "url": url,
                "published": published.isoformat(),
                "date_label": published.strftime("%b. %d, %Y").replace(" 0", " "),
                "origin": "Google News RSS",
            }
        )

    return sorted(items, key=lambda item: item["published"], reverse=True)[:max_items], None


def fetch_federal_register(
    window_start: datetime,
    window_end: datetime,
) -> tuple[list[dict[str, Any]], list[str]]:
    endpoint = "https://www.federalregister.gov/api/v1/documents.json"
    items: list[dict[str, Any]] = []
    errors: list[str] = []

    for term in FEDERAL_REGISTER_TERMS:
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
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            errors.append(f'Federal Register search "{term}": {exc}')
            continue

        for result in data.get("results", []):
            publication_date = result.get("publication_date", "")
            try:
                # FederalRegister.gov exposes a publication date rather than a precise
                # timestamp in this endpoint. Noon Eastern keeps the record within its
                # stated publication day for the daily window.
                published = datetime.fromisoformat(publication_date).replace(
                    hour=12, tzinfo=EASTERN
                )
            except ValueError:
                continue
            if published < window_start or published > window_end:
                continue

            title = clean_spaces(result.get("title", "Untitled federal action"))
            agencies = ", ".join(
                agency.get("name", "")
                for agency in result.get("agencies", [])
                if agency.get("name")
            )
            abstract = strip_html(result.get("abstract", ""))
            if len(abstract) > 600:
                abstract = abstract[:597].rsplit(" ", 1)[0] + "…"
            url = result.get("html_url") or result.get("pdf_url") or ""

            items.append(
                {
                    "id": stable_id("Federal Register", url, title),
                    "search_section": "Federal Actions",
                    "title": title,
                    "summary": abstract,
                    "source": agencies or "Federal Register",
                    "url": url,
                    "published": published.isoformat(),
                    "date_label": published.strftime("%b. %d, %Y").replace(" 0", " "),
                    "origin": "Federal Register API",
                }
            )

    return items, errors


def deduplicate_articles(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda value: value["published"], reverse=True):
        key = (normalize_title(item["title"]), item.get("source", "").casefold())
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def collect_articles(
    window_start: datetime,
    window_end: datetime,
) -> tuple[list[dict[str, Any]], list[str]]:
    items: list[dict[str, Any]] = []
    errors: list[str] = []

    for section, queries in NEWS_QUERIES.items():
        for query in queries:
            found, error = fetch_google_news(
                section,
                query,
                window_start,
                window_end,
                max_items=25,
            )
            items.extend(found)
            if error:
                errors.append(error)

    federal_items, federal_errors = fetch_federal_register(window_start, window_end)
    items.extend(federal_items)
    errors.extend(federal_errors)
    return deduplicate_articles(items), errors


def analysis_schema() -> dict[str, Any]:
    cluster = {
        "type": "object",
        "properties": {
            "cluster_id": {"type": "string"},
            "article_ids": {"type": "array", "items": {"type": "string"}},
            "primary_article_id": {"type": "string"},
            "section": {"type": "string", "enum": TOPIC_SECTIONS},
            "relevant": {"type": "boolean"},
            "importance": {"type": "integer", "minimum": 1, "maximum": 10},
            "canonical_title": {"type": "string"},
            "summary": {"type": "string"},
            "is_administration_win": {"type": "boolean"},
            "eo_number": {"type": "string"},
            "eo_section": {"type": "string"},
            "win_explanation": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "exclude_reason": {"type": "string"},
        },
        "required": [
            "cluster_id", "article_ids", "primary_article_id", "section",
            "relevant", "importance", "canonical_title", "summary",
            "is_administration_win", "eo_number", "eo_section",
            "win_explanation", "confidence", "exclude_reason",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "executive_summary": {"type": "string"},
            "what_to_watch": {"type": "array", "items": {"type": "string"}},
            "clusters": {"type": "array", "items": cluster},
        },
        "required": ["executive_summary", "what_to_watch", "clusters"],
        "additionalProperties": False,
    }


def prompt_messages(
    articles: list[dict[str, Any]],
    window_start: datetime,
    window_end: datetime,
) -> list[dict[str, str]]:
    compact = [
        {
            "id": item["id"],
            "search_section": item["search_section"],
            "title": item["title"],
            "summary": item.get("summary", ""),
            "source": item["source"],
            "published": item["published"],
            "origin": item["origin"],
        }
        for item in articles
    ]

    developer = f"""
You are the senior editor of a concise daily U.S. advanced-transportation news update.
The coverage window is exactly {window_start.isoformat()} through {window_end.isoformat()}.
Analyze only the supplied public-source records. Do not invent facts or infer details that
are not supported by a headline or snippet.

EDITORIAL PURPOSE
- Produce a short, useful executive briefing for readers focused on U.S. drones, C-UAS,
  advanced air mobility, autonomous vehicles, civil supersonics, rail innovation, and
  related federal actions.
- The voice may be administration-forward and confidently pro-American. Clearly credit
  President Trump or his Administration only when a direct, supportable causal connection
  exists. Do not use campaign slogans, advocacy language, or unsupported praise.
- This is a news aggregator as well as an executive briefing. Include every substantively
  relevant U.S. sector development, even when it is operational, commercial, technical,
  state-level, research-oriented, or industry-led rather than a major federal policy action.
- Do not reject a relevant story merely because it is not transformational. Exclude only
  genuinely tangential, duplicative, promotional, or low-information material.

EXECUTIVE SUMMARY
- Write 2 or 3 sentences, 55-90 words total.
- Explain the overall pattern of the day rather than listing every headline.
- Identify the most consequential development and major movement across the portfolio.
- Mention that a field was quiet only when useful.

RELEVANCE
- Include credible U.S. developments involving deployments, approvals, waivers,
  certifications, testing, manufacturing, contracts, partnerships, investment, facilities,
  research, enforcement, legislation, rulemaking, safety, operations, and market expansion.
- Exclude consumer products, generic AI/software features, celebrity commentary, pure stock
  promotion or valuation pieces, generic market-size reports, routine foreign news without
  a material U.S. implication, and keyword collisions.
- A story may be relevant with a low importance score. Use relevance to answer "does this
  belong in this portfolio?" and importance to answer "how prominently should it appear?"
- Aim to retain roughly 3-8 distinct stories per topic when the supplied candidates support
  that many, without inventing or padding.

CLUSTERING
- Cluster only articles covering the same concrete event, announcement, rule, deployment,
  flight, contract, facility, study, or government action.
- Sharing a broad topic is not enough. Different cities, companies, deployments, rules,
  contracts, or studies are separate stories.
- Most clusters should have 1-4 articles. More than five should be exceptionally rare.
- Avoid umbrella headlines such as "market activity grows" or "stories span...".

SUMMARIES
- Canonical titles should be factual and specific.
- Write 1-2 sentences, no more than 60 words, using only supplied information.
- Select the strongest primary source using this preference: {SOURCE_PREFERENCE}

TRUMP ADMINISTRATION WINS
- A win requires a direct, supportable connection to an Administration action, EO mandate,
  rule, approval, federal program, enforcement result, domestic-industrial-base outcome,
  or removal of a regulatory barrier.
- A positive private-sector story is not automatically an Administration win.
- For a true win, use exactly one of EO 14307, EO 14305, or EO 14304 only when applicable.
  If another verified Administration action is the basis, leave EO fields blank.
- Write one 30-55 word win explanation focused on the concrete American result. The app
  displays the EO's full title separately, so do not begin "This is a win for EO...".
- If the causal connection is uncertain, set is_administration_win false.

WHAT TO WATCH
- Return 0-3 concise items, each no more than 28 words.
- Include only pending actions, expected next steps, deadlines, or unresolved developments
  reasonably supported by the supplied stories. Do not invent forecasts.

{EO_REFERENCE}
""".strip()

    user = "Analyze these candidate records:\n" + json.dumps(compact, ensure_ascii=False)
    return [
        {"role": "developer", "content": developer},
        {"role": "user", "content": user},
    ]


def extract_response_text(data: dict[str, Any]) -> str:
    text_parts: list[str] = []
    refusals: list[str] = []
    for output_item in data.get("output", []):
        if output_item.get("type") != "message":
            continue
        for content_item in output_item.get("content", []):
            if content_item.get("type") == "output_text":
                text_parts.append(content_item.get("text", ""))
            elif content_item.get("type") == "refusal":
                refusals.append(content_item.get("refusal", "Request refused."))
    if refusals:
        raise RuntimeError("OpenAI declined the request: " + " ".join(refusals))
    text = "".join(text_parts).strip()
    if not text:
        raise RuntimeError("OpenAI returned no usable output.")
    return text


def estimate_cost(model: str, usage: dict[str, Any]) -> float | None:
    prices = OPENAI_TOKEN_PRICES.get(model)
    if not prices:
        return None
    return (
        int(usage.get("input_tokens", 0) or 0) * prices["input"] / 1_000_000
        + int(usage.get("output_tokens", 0) or 0) * prices["output"] / 1_000_000
    )


def analyze_articles(
    articles: list[dict[str, Any]],
    api_key: str,
    model: str,
    window_start: datetime,
    window_end: datetime,
) -> tuple[dict[str, Any], dict[str, Any], float | None]:
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing.")

    payload = {
        "model": model,
        "input": prompt_messages(articles, window_start, window_end),
        "reasoning": {"effort": "none"},
        "max_output_tokens": 16000,
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "transportation_news_analysis",
                "strict": True,
                "schema": analysis_schema(),
            }
        },
    }

    errors: list[str] = []
    for attempt in range(4):
        try:
            response = requests.post(
                OPENAI_RESPONSES_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=240,
            )
        except requests.RequestException as exc:
            errors.append(f"network error: {exc}")
            if attempt < 3:
                time.sleep((2 ** attempt) + random.uniform(0.2, 1.0))
                continue
            break

        if response.status_code < 400:
            data = response.json()
            analysis = json.loads(extract_response_text(data))
            usage = data.get("usage") or {}
            return analysis, usage, estimate_cost(model, usage)

        detail = clean_spaces(response.text)[:700]
        errors.append(f"HTTP {response.status_code}: {detail}")
        if response.status_code in TRANSIENT_OPENAI_STATUS_CODES and attempt < 3:
            time.sleep((2 ** attempt) + random.uniform(0.2, 1.0))
            continue
        raise RuntimeError(f"OpenAI API returned HTTP {response.status_code}: {detail}")

    raise RuntimeError("OpenAI request failed after retries: " + " | ".join(errors[-4:]))


def validate_analysis(
    analysis: dict[str, Any],
    articles: list[dict[str, Any]],
) -> dict[str, Any]:
    known = {item["id"] for item in articles}
    clusters: list[dict[str, Any]] = []
    used: set[str] = set()

    for raw in analysis.get("clusters", []):
        ids = [article_id for article_id in raw.get("article_ids", []) if article_id in known]
        primary = raw.get("primary_article_id", "")
        if primary in known and primary not in ids:
            ids.insert(0, primary)
        ids = [article_id for article_id in ids if article_id not in used]
        if not ids:
            continue
        if primary not in ids:
            primary = ids[0]
        used.update(ids)

        section = raw.get("section", "")
        if section not in TOPIC_SECTIONS:
            section = articles[0].get("search_section", "UAS and Drones")
            if section not in TOPIC_SECTIONS:
                section = "UAS and Drones"

        eo_number = clean_spaces(raw.get("eo_number", ""))
        if eo_number not in EO_DISPLAY_NAMES:
            eo_number = ""

        clusters.append(
            {
                "cluster_id": clean_spaces(raw.get("cluster_id", "")) or stable_id(*ids),
                "article_ids": ids,
                "primary_article_id": primary,
                "section": section,
                "relevant": bool(raw.get("relevant", False)),
                "importance": max(1, min(10, int(raw.get("importance", 1) or 1))),
                "canonical_title": clean_spaces(raw.get("canonical_title", "")),
                "summary": clean_spaces(raw.get("summary", "")),
                "is_administration_win": bool(raw.get("is_administration_win", False)),
                "eo_number": eo_number,
                "eo_section": clean_spaces(raw.get("eo_section", "")),
                "win_explanation": clean_spaces(raw.get("win_explanation", "")),
                "confidence": raw.get("confidence", "low"),
                "exclude_reason": clean_spaces(raw.get("exclude_reason", "")),
            }
        )

    return {
        "executive_summary": clean_spaces(analysis.get("executive_summary", "")),
        "what_to_watch": [
            clean_spaces(item)
            for item in analysis.get("what_to_watch", [])[:3]
            if clean_spaces(item)
        ],
        "clusters": clusters,
    }


def cluster_to_story(
    cluster: dict[str, Any],
    lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    primary = lookup[cluster["primary_article_id"]]
    related: list[dict[str, str]] = []
    seen_sources = {primary.get("source", "").casefold()}
    for article_id in cluster["article_ids"]:
        if article_id == cluster["primary_article_id"]:
            continue
        article = lookup.get(article_id)
        if not article:
            continue
        source_key = article.get("source", "").casefold()
        if not source_key or source_key in seen_sources:
            continue
        seen_sources.add(source_key)
        related.append({"source": article["source"], "url": article["url"]})

    title = cluster["canonical_title"] or primary["title"]
    return {
        "id": cluster["cluster_id"],
        "title": title,
        "summary": cluster["summary"],
        "source": primary["source"],
        "url": primary["url"],
        "published": primary["published"],
        "date_label": primary["date_label"],
        "section": cluster["section"],
        "importance": cluster["importance"],
        "confidence": cluster["confidence"],
        "is_administration_win": cluster["is_administration_win"],
        "eo_number": cluster["eo_number"],
        "eo_name": EO_DISPLAY_NAMES.get(cluster["eo_number"], ""),
        "eo_section": cluster["eo_section"],
        "win_explanation": cluster["win_explanation"],
        "also_covered": related[:8],
    }


def arrange_sections(stories: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    sections = {section: [] for section in SECTION_ORDER}
    relevant = sorted(stories, key=lambda item: (item["importance"], item["published"]), reverse=True)

    wins = [item for item in relevant if item["is_administration_win"]]
    sections["Trump Administration Wins"] = wins[:6]
    win_ids = {item["id"] for item in sections["Trump Administration Wins"]}

    eligible_top = [
        item for item in relevant
        if item["id"] not in win_ids and item["importance"] >= 7
    ]
    sections["Top Developments"] = eligible_top[:6]
    top_ids = {item["id"] for item in sections["Top Developments"]}

    for item in relevant:
        if item["id"] in win_ids or item["id"] in top_ids:
            continue
        section = item["section"]
        if section in sections and len(sections[section]) < 10:
            sections[section].append(item)

    return sections


def generate_daily_briefing(
    api_key: str,
    model: str = DEFAULT_OPENAI_MODEL,
    window_end: datetime | None = None,
) -> dict[str, Any]:
    end = (window_end or datetime.now(EASTERN)).astimezone(EASTERN)
    start = end - timedelta(hours=24)
    articles, source_errors = collect_articles(start, end)

    if not articles:
        return {
            "generated_at": datetime.now(EASTERN).isoformat(),
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "model": model,
            "usage": {},
            "estimated_cost": None,
            "executive_summary": "No consequential new public-source developments were identified in the preceding 24 hours.",
            "what_to_watch": [],
            "sections": {section: [] for section in SECTION_ORDER},
            "source_errors": source_errors,
            "candidate_count": 0,
        }

    raw_analysis, usage, cost = analyze_articles(articles, api_key, model, start, end)
    analysis = validate_analysis(raw_analysis, articles)
    lookup = {item["id"]: item for item in articles}
    stories = [
        cluster_to_story(cluster, lookup)
        for cluster in analysis["clusters"]
        if cluster["relevant"] and cluster["primary_article_id"] in lookup
    ]

    arranged = arrange_sections(stories)
    candidate_counts: dict[str, int] = {}
    for article in articles:
        key = article.get("search_section", "Unknown")
        candidate_counts[key] = candidate_counts.get(key, 0) + 1

    included_counts = {
        section: len(items)
        for section, items in arranged.items()
    }

    return {
        "generated_at": datetime.now(EASTERN).isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "model": model,
        "usage": usage,
        "estimated_cost": cost,
        "executive_summary": analysis["executive_summary"],
        "what_to_watch": analysis["what_to_watch"],
        "sections": arranged,
        "source_errors": source_errors,
        "candidate_count": len(articles),
        "candidate_counts": candidate_counts,
        "included_counts": included_counts,
    }


def write_briefing(briefing: dict[str, Any], repository_root: Path) -> tuple[Path, Path]:
    data_dir = repository_root / "data"
    archive_dir = data_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    latest_path = data_dir / "latest_briefing.json"
    date_label = datetime.fromisoformat(briefing["window_end"]).astimezone(EASTERN).date().isoformat()
    archive_path = archive_dir / f"{date_label}.json"

    payload = json.dumps(briefing, indent=2, ensure_ascii=False) + "\n"
    latest_path.write_text(payload, encoding="utf-8")
    archive_path.write_text(payload, encoding="utf-8")
    return latest_path, archive_path
