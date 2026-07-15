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
        '("autonomous vehicle" OR "automated vehicle" OR "automated driving system") (NHTSA OR DOT OR United States OR U.S.)',
        '(robotaxi OR "self-driving" OR driverless) (deployment OR expansion OR permit OR regulation OR operations) United States',
        '("automated driving system" OR "ADS-equipped" OR driverless) (NHTSA OR FMVSS OR exemption OR rulemaking OR compliance)',
        '(FMVSS OR "Federal Motor Vehicle Safety Standards") ("automated driving" OR autonomous OR driverless OR ADS)',
        '("FMVSS 102" OR "FMVSS 103" OR "FMVSS 104" OR "FMVSS 108") (ADS OR autonomous OR automated)',
        '("Part 555" OR "temporary exemption") (autonomous vehicle OR automated driving OR ADS)',
        '(FMCSA OR "commercial motor vehicle") ("automated driving system" OR autonomous truck OR driverless truck)',
        '("autonomous truck" OR "driverless truck" OR "self-driving truck") (deployment OR operations OR regulation OR safety) United States',
        '(Waymo OR Cruise OR Zoox OR Tesla OR Aurora OR Motional OR Gatik OR Kodiak OR Torc OR Waabi OR Nuro OR "May Mobility") (robotaxi OR autonomous vehicle OR driverless OR self-driving)',
        '("autonomous vehicle" OR robotaxi OR "automated driving system") (crash OR safety OR investigation OR recall OR enforcement)',
        '(state legislature OR governor OR city OR DMV) ("autonomous vehicle" OR robotaxi OR driverless)',
        '("vehicle-to-everything" OR V2X OR "roadside infrastructure") ("automated driving" OR autonomous vehicle)',
        '("autonomous vehicle" OR "automated driving") (simulation OR mapping OR validation OR testing) United States',
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
    "automated driving system",
    "ADS-equipped commercial motor vehicle",
    "Federal Motor Vehicle Safety Standards",
    "FMVSS",
    "Part 555",
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


TRANSPORTATION_AGENCY_MARKERS = (
    "department of transportation",
    "federal aviation administration",
    "national highway traffic safety administration",
    "federal motor carrier safety administration",
    "federal railroad administration",
    "federal transit administration",
    "pipeline and hazardous materials safety administration",
    "maritime administration",
    "transportation security administration",
)

PORTFOLIO_ANCHORS = (
    "drone", "unmanned aircraft", "uncrewed aircraft", "uas", "counter-uas",
    "counter uas", "c-uas", "counter drone", "airspace sovereignty",
    "beyond visual line of sight", "bvlos", "remote id", "part 107",
    "advanced air mobility", "aam", "evtol", "air taxi", "powered-lift",
    "vertiport", "electric aircraft", "autonomous vehicle", "automated vehicle",
    "automated driving", "automated driving system", "ads-equipped", "robotaxi",
    "self-driving", "driverless", "autonomous truck", "driverless truck",
    "nhtsa", "fmvss", "fmcsa", "part 555", "vehicle-to-everything", "v2x",
    "supersonic", "x-59", "boom overture", "hermeus", "high-speed rail",
    "bullet train", "maglev", "autonomous rail", "passenger rail",
)

OBVIOUS_NON_PORTFOLIO_MARKERS = (
    "psychedelic", "psilocybin", "mental health therapy", "drug therapy",
    "clinical trial", "medicare", "medicaid", "public health emergency",
)


def automated_record_is_portfolio_relevant(record: dict[str, Any]) -> bool:
    """Conservative pre-AI guard against obvious keyword collisions."""
    if record.get("required_include", False):
        return True

    text = clean_spaces(
        " ".join(
            str(record.get(key, ""))
            for key in ("title", "summary", "description", "source")
        )
    ).casefold()

    has_anchor = any(marker in text for marker in PORTFOLIO_ANCHORS)
    has_transport_agency = any(
        marker in text for marker in TRANSPORTATION_AGENCY_MARKERS
    )

    if record.get("origin") == "Federal Register API":
        return has_transport_agency or has_anchor

    if any(marker in text for marker in OBVIOUS_NON_PORTFOLIO_MARKERS):
        return has_anchor

    return True


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

    filtered = [
        item for item in items
        if automated_record_is_portfolio_relevant(item)
    ]
    return deduplicate_articles(filtered), errors



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
            "win_event_within_window": {"type": "boolean"},
            "win_direct_administration_nexus": {"type": "boolean"},
            "win_concrete_american_benefit": {"type": "boolean"},
            "win_foreign_company_expansion_only": {"type": "boolean"},
            "eo_number": {"type": "string"},
            "eo_section": {"type": "string"},
            "win_explanation": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "exclude_reason": {"type": "string"},
        },
        "required": [
            "cluster_id", "article_ids", "primary_article_id", "section",
            "relevant", "importance", "canonical_title", "summary",
            "is_administration_win", "win_event_within_window",
            "win_direct_administration_nexus", "win_concrete_american_benefit",
            "win_foreign_company_expansion_only", "eo_number", "eo_section",
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


def best_record_title(record: dict[str, Any]) -> str:
    candidates = [
        record.get("title", ""),
        record.get("original_title", ""),
        record.get("pasted_headline", ""),
        record.get("pasted_context", ""),
    ]
    source = clean_spaces(record.get("source", "")).casefold()
    for value in candidates:
        value = clean_spaces(value)
        if not value:
            continue
        if value.casefold() == source:
            continue
        if value.lower().startswith(("http://", "https://")):
            continue
        if len(value) >= 10:
            return value[:240]
    return clean_spaces(record.get("url", "Untitled supplemental item"))


def infer_section(record: dict[str, Any]) -> str:
    text = " ".join(
        str(record.get(key, ""))
        for key in ("title", "summary", "description", "pasted_context", "search_section")
    ).lower()
    if any(term in text for term in (
        "counter-uas", "counter uas", "c-uas", "counter drone", "drone detection",
        "drone mitigation", "airspace sovereignty", "unauthorized drone",
    )):
        return "UAS Security and C-UAS"
    if any(term in text for term in (
        "evtol", "eipp", "advanced air mobility", "air taxi", "powered-lift",
        "vertiport", "electric aircraft",
    )):
        return "eVTOL Integration Pilot Program and AAM"
    if any(term in text for term in (
        "autonomous vehicle", "automated vehicle", "robotaxi", "self-driving",
        "driverless", "automated driving", "automated driving system",
        "ads-equipped", "autonomous truck", "driverless truck", "waymo",
        "cruise", "zoox", "aurora", "motional", "gatik", "kodiak",
        "torc", "waabi", "nuro", "may mobility", "nhtsa", "fmvss",
        "fmcsa", "part 555", "vehicle-to-everything", "v2x",
    )):
        return "Autonomous Vehicles"
    if any(term in text for term in (
        "supersonic", "x-59", "high-speed rail", "bullet train", "maglev",
        "passenger rail", "autonomous rail",
    )):
        return "Other Advanced Transportation"
    if record.get("origin") == "Federal Register API":
        return "Federal Actions"
    return "UAS and Drones"


def prompt_messages(
    articles: list[dict[str, Any]],
    window_start: datetime,
    window_end: datetime,
) -> list[dict[str, str]]:
    compact = [
        {
            "id": item["id"],
            "search_section": item.get("search_section", ""),
            "title": item.get("title", ""),
            "original_title": item.get("original_title", ""),
            "summary": item.get("summary", ""),
            "description": item.get("description", ""),
            "pasted_headline": item.get("pasted_headline", ""),
            "pasted_context": item.get("pasted_context", ""),
            "source": item.get("source", ""),
            "published": item.get("published", ""),
            "origin": item.get("origin", ""),
            "required_include": bool(item.get("required_include", False)),
        }
        for item in articles
    ]

    developer = f"""
You are the senior editor of a daily U.S. advanced-transportation news update.
The coverage window is {window_start.isoformat()} through {window_end.isoformat()}.
Analyze only the supplied public-source records. Do not invent facts.

The records combine:
1. An automated raw news collection from the preceding 24 hours.
2. Supplemental items pasted by the editor from another daily news email.

MANDATORY SUPPLEMENTAL-ITEM RULE
- Every record with required_include=true MUST be accounted for.
- A required item must either be the primary article in a distinct story or appear in
  article_ids as genuine same-event coverage of another story.
- Never discard a required item merely because it is low importance or imperfectly formatted.
- Never use a publisher name such as "MSN", "AOL", or "Yahoo" as the canonical headline.
- Use the pasted headline, surrounding context, fetched metadata, and URL information to
  write a specific factual headline.
- Never use generic filler such as "Imported from the supplemental daily news email."
- Different events must remain separate stories. Merge only true same-event coverage.

EDITORIAL SCOPE AND RELEVANCE
- Produce a useful, fairly comprehensive briefing on:
  1. UAS and drones.
  2. UAS security and counter-UAS.
  3. eIPP, eVTOL, powered-lift, and advanced air mobility.
  4. Autonomous vehicles and automated driving.
  5. Civil supersonics and genuinely advanced rail or transportation technology.
  6. Directly relevant Federal actions.
- Autonomous Vehicles explicitly includes robotaxis, privately owned automated vehicles,
  autonomous trucking, ADS-equipped commercial motor vehicles, NHTSA and FMCSA actions,
  FMVSS modernization, Part 555 exemptions, recalls, investigations, permits, state and
  local laws, deployments, testing, simulation, mapping, and V2X when directly connected
  to automated driving.
- Place AV-specific Federal actions, including FMVSS and NHTSA/FMCSA ADS actions, in the
  Autonomous Vehicles section rather than the generic Federal Actions section.
- Include substantive operational, commercial, technical, regulatory, state, research,
  manufacturing, contract, deployment, safety, and enforcement developments.
- Be strict about relevance. Exclude health policy, medicine, pharmaceuticals, HHS, NIH,
  FDA, psychedelic therapies, generic AI, unrelated energy stories, and other keyword
  collisions unless the record has a direct and material connection to one of the
  transportation categories above.
- Exclude pure stock promotion, generic market-size reports, consumer-product lists,
  celebrity commentary, and articles that merely repeat old news without a new development.
- A required supplemental item must still be accounted for, but unrelated automated records
  must be marked relevant=false and excluded.
- The voice may be confidently pro-American and Administration-forward, but credit President
  Trump or his Administration only where a direct, supportable connection exists.

CLUSTERING
- Cluster only records covering the same concrete event, announcement, rule, deployment,
  flight, contract, facility, study, or government action.
- Different companies, cities, contracts, tests, rules, or deployments are separate stories.
- Most clusters should contain 1-4 records.
- Do not create broad umbrella stories such as "drone activity expands" or "market activity grows."

HEADLINES AND SUMMARIES
- Every distinct story must receive a specific canonical headline.
- Write 1-2 concise factual sentences, normally 35-70 words.
- Select the strongest primary source using this preference: {SOURCE_PREFERENCE}
- Preserve every required supplemental URL either as primary coverage or "Also covered by."

EXECUTIVE SUMMARY
- Write 2-3 sentences, 60-100 words.
- Describe the day's overall pattern and most consequential developments.

TRUMP ADMINISTRATION WINS — HARD ELIGIBILITY TEST
A story may be labeled a win only when ALL of the following are true:
1. The underlying event, decision, milestone, approval, deployment, contract, enforcement
   result, or rulemaking action actually occurred during the stated 24-hour coverage window.
   Publication of a new article during the window is not enough.
2. There is a direct, supportable causal or operational nexus to a Trump Administration
   action, EO mandate, rule, approval, federal program, enforcement action, or removal of
   a regulatory barrier. Do not infer credit merely because a story is generally consistent
   with an EO.
3. The event produces a concrete American benefit: U.S. capability, domestic manufacturing,
   American jobs, public safety, national security, operational deployment, regulatory
   progress, or removal of a barrier for American innovation.
4. The story is not merely a foreign-headquartered company entering, expanding in, selling
   into, or investing in the United States. Foreign-company U.S. expansion alone is NOT an
   Administration win and must remain an ordinary sector story.

Set the four structured win fields carefully:
- win_event_within_window=true only for a genuinely new event in the 24-hour window.
- win_direct_administration_nexus=true only when the record itself supports the nexus.
- win_concrete_american_benefit=true only for a specific, tangible U.S. result.
- win_foreign_company_expansion_only=true when the claimed benefit is simply a foreign
  company's U.S. entry, expansion, sales, investment, office, or facility.

STALE-NEWS EXAMPLES
- An article published today discussing an NPRM issued weeks ago is not a new win.
- A retrospective, explainer, opinion piece, or recap of an earlier Administration action
  is not a win.
- A new comment deadline, hearing, OIRA movement, final rule, approval, contract award,
  implementation milestone, or operational launch during the window may qualify if the
  other conditions are met.

- A positive private-sector story is not automatically an Administration win.
- When applicable, use exactly EO 14307, EO 14305, or EO 14304 and identify the section.
- Write one 30-55 word explanation focused on the concrete American result.
- The app displays the full EO name separately, so do not begin "This is a win for EO...".

WHAT TO WATCH
- Return 0-3 concise, supportable next steps, deadlines, or unresolved developments.

{EO_REFERENCE}
""".strip()

    user = "Build the briefing from these records:\n" + json.dumps(
        compact, ensure_ascii=False
    )
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
        "max_output_tokens": 20000,
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
                timeout=300,
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

        detail = clean_spaces(response.text)[:900]
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
    lookup = {item["id"]: item for item in articles}
    known = set(lookup)
    required = {
        item["id"] for item in articles if item.get("required_include", False)
    }
    clusters: list[dict[str, Any]] = []
    used: set[str] = set()

    for raw in analysis.get("clusters", []):
        ids = [
            article_id for article_id in raw.get("article_ids", [])
            if article_id in known and article_id not in used
        ]
        primary = raw.get("primary_article_id", "")
        if primary in known and primary not in ids and primary not in used:
            ids.insert(0, primary)
        if not ids:
            continue
        if primary not in ids:
            primary = ids[0]
        used.update(ids)

        section = raw.get("section", "")
        if section not in TOPIC_SECTIONS:
            section = infer_section(lookup[primary])

        eo_number = clean_spaces(raw.get("eo_number", ""))
        if eo_number not in EO_DISPLAY_NAMES:
            eo_number = ""

        includes_required = any(article_id in required for article_id in ids)

        win_event_within_window = bool(raw.get("win_event_within_window", False))
        win_direct_nexus = bool(
            raw.get("win_direct_administration_nexus", False)
        )
        win_american_benefit = bool(
            raw.get("win_concrete_american_benefit", False)
        )
        win_foreign_expansion_only = bool(
            raw.get("win_foreign_company_expansion_only", False)
        )
        validated_win = (
            bool(raw.get("is_administration_win", False))
            and win_event_within_window
            and win_direct_nexus
            and win_american_benefit
            and not win_foreign_expansion_only
            and bool(eo_number)
        )

        if not validated_win:
            eo_number = ""
            eo_section = ""
            win_explanation = ""
        else:
            eo_section = clean_spaces(raw.get("eo_section", ""))
            win_explanation = clean_spaces(raw.get("win_explanation", ""))

        clusters.append(
            {
                "cluster_id": clean_spaces(raw.get("cluster_id", "")) or stable_id(*ids),
                "article_ids": ids,
                "primary_article_id": primary,
                "section": section,
                "relevant": bool(raw.get("relevant", False)) or includes_required,
                "importance": max(1, min(10, int(raw.get("importance", 1) or 1))),
                "canonical_title": clean_spaces(raw.get("canonical_title", "")),
                "summary": clean_spaces(raw.get("summary", "")),
                "is_administration_win": validated_win,
                "eo_number": eo_number,
                "eo_section": eo_section,
                "win_explanation": win_explanation,
                "confidence": raw.get("confidence", "low"),
                "exclude_reason": clean_spaces(raw.get("exclude_reason", "")),
            }
        )

    # Guarantee that every supplemental item is represented. This should be rare,
    # because the prompt already requires it, but it prevents silent loss.
    missing_required = required - used
    for article_id in sorted(missing_required):
        record = lookup[article_id]
        title = best_record_title(record)
        description = clean_spaces(
            record.get("description", "")
            or record.get("summary", "")
            or record.get("pasted_context", "")
        )
        if not description or description.casefold() == title.casefold():
            description = f"{title}."
        clusters.append(
            {
                "cluster_id": stable_id("required", article_id),
                "article_ids": [article_id],
                "primary_article_id": article_id,
                "section": infer_section(record),
                "relevant": True,
                "importance": 3,
                "canonical_title": title,
                "summary": description[:500],
                "is_administration_win": False,
                "eo_number": "",
                "eo_section": "",
                "win_explanation": "",
                "confidence": "low",
                "exclude_reason": "",
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
        "required_count": len(required),
        "required_accounted_count": len(required),
    }


def cluster_to_story(
    cluster: dict[str, Any],
    lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    primary = lookup[cluster["primary_article_id"]]
    related: list[dict[str, str]] = []
    seen_urls = {primary.get("url", "")}
    seen_sources = {primary.get("source", "").casefold()}

    for article_id in cluster["article_ids"]:
        if article_id == cluster["primary_article_id"]:
            continue
        article = lookup.get(article_id)
        if not article:
            continue
        url = article.get("url", "")
        source = article.get("source", "") or "Related coverage"
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        # Preserve supplemental links even if the publisher repeats.
        if source.casefold() in seen_sources and not article.get("required_include"):
            continue
        seen_sources.add(source.casefold())
        related.append({"source": source, "url": url})

    title = cluster["canonical_title"] or best_record_title(primary)
    summary = cluster["summary"] or clean_spaces(
        primary.get("description", "")
        or primary.get("summary", "")
        or primary.get("pasted_context", "")
    )

    return {
        "id": cluster["cluster_id"],
        "title": title,
        "summary": summary,
        "source": primary.get("source", "Source"),
        "url": primary.get("url", ""),
        "published": primary.get("published", datetime.now(EASTERN).isoformat()),
        "date_label": primary.get(
            "date_label",
            datetime.now(EASTERN).strftime("%b. %d, %Y").replace(" 0", " "),
        ),
        "section": cluster["section"],
        "importance": cluster["importance"],
        "confidence": cluster["confidence"],
        "is_administration_win": cluster["is_administration_win"],
        "eo_number": cluster["eo_number"],
        "eo_name": EO_DISPLAY_NAMES.get(cluster["eo_number"], ""),
        "eo_section": cluster["eo_section"],
        "win_explanation": cluster["win_explanation"],
        "also_covered": related,
        "contains_supplemental": any(
            lookup[article_id].get("required_include", False)
            for article_id in cluster["article_ids"]
            if article_id in lookup
        ),
    }


def arrange_sections(stories: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    sections = {section: [] for section in SECTION_ORDER}
    relevant = sorted(
        stories,
        key=lambda item: (item["importance"], item.get("published", "")),
        reverse=True,
    )

    wins = [item for item in relevant if item["is_administration_win"]]
    sections["Trump Administration Wins"] = wins[:8]
    win_ids = {item["id"] for item in sections["Trump Administration Wins"]}

    eligible_top = [
        item for item in relevant
        if item["id"] not in win_ids and item["importance"] >= 7
    ]
    sections["Top Developments"] = eligible_top[:8]
    top_ids = {item["id"] for item in sections["Top Developments"]}

    for item in relevant:
        if item["id"] in win_ids or item["id"] in top_ids:
            continue
        section = item["section"]
        if section in sections:
            sections[section].append(item)

    return sections


def generate_raw_feed(window_end: datetime | None = None) -> dict[str, Any]:
    end = (window_end or datetime.now(EASTERN)).astimezone(EASTERN)
    start = end - timedelta(hours=24)
    articles, source_errors = collect_articles(start, end)

    # Keep broad coverage while preventing one query family from swamping the feed.
    grouped: dict[str, list[dict[str, Any]]] = {}
    for article in articles:
        grouped.setdefault(article.get("search_section", "Unknown"), []).append(article)

    capped: list[dict[str, Any]] = []
    for _, records in grouped.items():
        capped.extend(records[:45])
    capped = deduplicate_articles(capped)

    counts: dict[str, int] = {}
    for article in capped:
        key = article.get("search_section", "Unknown")
        counts[key] = counts.get(key, 0) + 1

    return {
        "generated_at": datetime.now(EASTERN).isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "articles": capped,
        "source_errors": source_errors,
        "candidate_count": len(capped),
        "candidate_counts": counts,
    }


def write_raw_feed(
    raw_feed: dict[str, Any],
    repository_root: Path,
) -> tuple[Path, Path]:
    data_dir = repository_root / "data"
    archive_dir = data_dir / "raw_archive"
    data_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    latest_path = data_dir / "latest_raw_news.json"
    date_label = (
        datetime.fromisoformat(raw_feed["window_end"])
        .astimezone(EASTERN)
        .date()
        .isoformat()
    )
    archive_path = archive_dir / f"{date_label}.json"

    payload = json.dumps(raw_feed, indent=2, ensure_ascii=False) + "\n"
    latest_path.write_text(payload, encoding="utf-8")
    archive_path.write_text(payload, encoding="utf-8")
    return latest_path, archive_path


def generate_briefing_from_records(
    raw_feed: dict[str, Any],
    supplemental_records: list[dict[str, Any]],
    api_key: str,
    model: str = DEFAULT_OPENAI_MODEL,
) -> dict[str, Any]:
    start = datetime.fromisoformat(raw_feed["window_start"]).astimezone(EASTERN)
    end = datetime.fromisoformat(raw_feed["window_end"]).astimezone(EASTERN)

    raw_automated = [dict(item) for item in raw_feed.get("articles", [])]
    automated = [
        item for item in raw_automated
        if automated_record_is_portfolio_relevant(item)
    ]
    supplemental = []
    for item in supplemental_records:
        record = dict(item)
        record["required_include"] = True
        record["origin"] = "Supplemental daily email"
        record["search_section"] = record.get("search_section") or "Supplemental email"
        record["published"] = record.get("published") or end.isoformat()
        record["date_label"] = record.get("date_label") or end.strftime(
            "%b. %d, %Y"
        ).replace(" 0", " ")
        record["id"] = record.get("id") or stable_id(
            "supplemental", record.get("url", ""), record.get("title", "")
        )
        supplemental.append(record)

    combined = deduplicate_articles(automated + supplemental)
    raw_analysis, usage, cost = analyze_articles(
        combined, api_key, model, start, end
    )
    analysis = validate_analysis(raw_analysis, combined)
    lookup = {item["id"]: item for item in combined}
    stories = [
        cluster_to_story(cluster, lookup)
        for cluster in analysis["clusters"]
        if cluster["relevant"] and cluster["primary_article_id"] in lookup
    ]
    arranged = arrange_sections(stories)

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
        "source_errors": raw_feed.get("source_errors", []),
        "candidate_count": len(combined),
        "raw_automated_candidate_count": len(raw_automated),
        "automated_candidate_count": len(automated),
        "automated_filtered_out_count": len(raw_automated) - len(automated),
        "supplemental_count": len(supplemental),
        "supplemental_accounted_count": analysis["required_accounted_count"],
        "candidate_counts": raw_feed.get("candidate_counts", {}),
        "included_counts": included_counts,
    }
