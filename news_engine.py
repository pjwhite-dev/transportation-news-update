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

from regulatory_tracker import build_regulatory_tracker

EASTERN = ZoneInfo("America/New_York")
OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
TRANSIENT_OPENAI_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}

EXECUTIVE_SUMMARY_PROCESS_MARKERS = (
    "supplemental",
    "automated feed",
    "automated record",
    "required item",
    "editorial pass",
    "editorial process",
    "source record",
    "record id",
    "article id",
    "link accounting",
    "coverage accounting",
    "links extracted",
    "links represented",
    "administration win",
    "administrative win",
    "qualifies as a win",
    "qualify as a win",
    "win eligibility",
    "win criteria",
    "win test",
    "win for the administration",
)

OPENAI_TOKEN_PRICES = {
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
    "gpt-5-mini": {"input": 0.25, "output": 2.00},
}

TOPIC_SECTIONS = [
    "UAS and Drones",
    "UAS Security and C-UAS",
    "Military",
    "eVTOL Integration Pilot Program and AAM",
    "Autonomous Vehicles",
    "Other Advanced Transportation",
    "International",
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
    "Military": [
        '(Pentagon OR DoD OR "Department of Defense") (drone OR UAS OR autonomous OR eVTOL)',
        '(Army OR Navy OR "Air Force" OR Marines OR "Space Force") (drone OR UAS OR autonomous aircraft)',
        '(DIU OR DARPA OR AFWERX OR SOCOM) (drone OR UAS OR autonomy OR advanced aviation)',
        '(military OR defense) (counter-UAS OR counter-drone OR drone contract OR autonomous system)',
        '(warfighter OR battlefield OR combat) (drone OR UAS OR autonomous vehicle)',
        '(defense contract OR military procurement) (drone OR UAS OR aircraft OR autonomy)',
        '(Ukraine OR Ukrainian OR Russia OR Russian) (drone OR UAS OR unmanned OR autonomous)',
        '(naval OR warship OR fleet) (drone OR unmanned OR autonomous OR missile)',
        '(missile OR munition OR "loitering munition" OR weapon) (drone OR autonomous)',
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
    "International": [
        '("autonomous vehicle" OR robotaxi OR driverless) (Europe OR China OR India OR Japan OR Canada)',
        '(eVTOL OR "advanced air mobility" OR "air taxi") (Europe OR Asia OR Middle East OR Canada)',
        '("high-speed rail" OR "hydrogen train" OR maglev) (Europe OR China OR India OR Japan)',
        '(drone OR UAS) (European Union OR United Kingdom OR Canada OR Japan) (rule OR operations OR deployment)',
        '("advanced transportation" OR "smart mobility") (Europe OR Asia OR Latin America)',
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

EO_SECTION_SUMMARIES = {
    "EO 14307": {
        "Section 3": (
            "advancing domestic commercialization of UAS technologies at scale"
        ),
        "Section 4(a)": "enabling routine BVLOS operations through FAA rulemaking",
        "Section 4(c)": "using AI to expedite Part 107 waiver reviews",
        "Section 5(a)": "updating the national civil-UAS integration roadmap",
        "Section 5(b)": (
            "using FAA UAS Test Ranges to accelerate testing and deployment"
        ),
        "Section 6": "accelerating safe U.S. eVTOL deployment through eIPP",
        "Section 7": (
            "strengthening the domestic drone industrial base and trusted supply chains"
        ),
    },
    "EO 14305": {
        "Section 3": (
            "protecting the public and mass gatherings from unlawful UAS activity"
        ),
        "Section 4": (
            "coordinating Federal airspace-security work through a dedicated task force"
        ),
        "Section 5": (
            "advancing Section 2209 fixed-site restrictions and security coordination"
        ),
        "Section 8": (
            "expanding protections for borders, airports, facilities, and military assets"
        ),
        "Section 9": (
            "building Federal and local counter-UAS capacity for major events"
        ),
    },
    "EO 14304": {
        "Section 2": (
            "removing obsolete barriers and establishing noise-based supersonic standards"
        ),
        "Section 3": (
            "coordinating supersonic research, testing, regulation, and integration"
        ),
        "Section 4": (
            "aligning international civil-supersonic regulation and safety agreements"
        ),
    },
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
    "pentagon", "department of defense", "military drone", "defense drone",
    "warfighter", "battlefield autonomy", "defense innovation unit", "afwerx",
)

OBVIOUS_NON_PORTFOLIO_MARKERS = (
    "psychedelic", "psilocybin", "mental health therapy", "drug therapy",
    "clinical trial", "medicare", "medicaid", "public health emergency",
)

MILITARY_SECTION_PATTERN = re.compile(
    r"\b(?:department of defen[sc]e|defen[sc]e department|"
    r"ministry of defen[sc]e|pentagon|dod|military|defen[sc]e|army|navy|air force|"
    r"marine corps|marines|space force|armed forces|warfighters?|battlefield|"
    r"combat|defen[sc]e contractor|defen[sc]e acquisition|nato|darpa|afwerx|"
    r"socom|northcom|diu|warships?|naval|"
    r"frigates?|destroyers?|battlefront|frontline|war zone|airstrikes?|"
    r"missiles?|munitions?|weapons?|loitering munition|kamikaze drone|"
    r"servicemembers?|troops?|soldiers?|warfare|hostilities|invasion|"
    r"drone strikes?|attack drones?|armed drones?|combat drones?|naval attacks?|"
    r"combat operations?|"
    r"anduril|shield ai|aerovironment|general atomics|northrop grumman|"
    r"lockheed martin|raytheon|l3harris|red cat|kratos)\b",
    re.IGNORECASE,
)

MILITARY_CONFLICT_ACTOR_PATTERN = re.compile(
    r"\b(?:ukraine|ukrainians?|russia|russians?)\b",
    re.IGNORECASE,
)

MILITARY_CONFLICT_CONTEXT_PATTERN = re.compile(
    r"\b(?:drones?|uas|uavs?|unmanned|attacks?|strikes?|hit|struck|sank|sunk|"
    r"ships?|vessels?|military|combat|war|warfare|battlefield|frontline|"
    r"weapons?|missiles?|munitions?|invasion|defen[sc]e)\b",
    re.IGNORECASE,
)

INTERNATIONAL_MARKER_PATTERN = re.compile(
    r"\b(?:africa|asia|australia|australian|austria|brazil|brazilian|britain|"
    r"british|canada|canadian|china|chinese|europe|european|finland|finnish|"
    r"france|french|germany|german|india|indian|indonesia|italy|italian|"
    r"japan|japanese|korea|korean|mexico|mexican|netherlands|norway|norwegian|"
    r"poland|polish|saudi arabia|singapore|spain|spanish|sweden|swedish|"
    r"switzerland|taiwan|taiwanese|united arab emirates|uae|united kingdom|"
    r"uk|vietnam|vietnamese|london|madrid|paris|berlin|tokyo|dubai|kolkata)\b",
    re.IGNORECASE,
)

DOMESTIC_MARKER_PATTERN = re.compile(
    r"\b(?:united states|u\.s|"
    r"(?<!latin )(?<!south )(?<!central )(?<!north )america(?:n)?|"
    r"department of transportation|transportation dept(?:artment)?|"
    r"faa|nhtsa|fmcsa|fra|fta|amtrak|penn station|california|texas|new york|"
    r"new jersey|washington,\s*d\.c|d\.c)\b",
    re.IGNORECASE,
)

COVERAGE_FLOOR_SECTIONS = (
    "Autonomous Vehicles",
    "Other Advanced Transportation",
    "International",
)

SECTION_COVERAGE_PATTERNS = {
    "Autonomous Vehicles": re.compile(
        r"\b(?:autonomous vehicles?|automated vehicles?|automated driving|"
        r"self-driving|driverless|robotaxis?|ads-equipped|autonomous trucks?|"
        r"waymo|zoox|nhtsa|fmvss|part 555|vehicle-to-everything|v2x)\b",
        re.IGNORECASE,
    ),
    "Other Advanced Transportation": re.compile(
        r"\b(?:civil supersonic|commercial supersonic|quiet supersonic|x-59|"
        r"boom supersonic|overture|hermeus|high-speed rail|bullet train|"
        r"hydrogen train|hydrogen fuel cell train|maglev|autonomous rail|"
        r"automated train|digital train|advanced rail)\b",
        re.IGNORECASE,
    ),
}

INTERNATIONAL_TRANSPORT_PATTERN = re.compile(
    r"\b(?:drones?|uas|uavs?|unmanned aircraft|evtol|advanced air mobility|"
    r"air taxis?|autonomous vehicles?|automated vehicles?|automated driving|"
    r"self-driving|driverless|robotaxis?|autonomous trucks?|high-speed rail|"
    r"bullet trains?|hydrogen trains?|hydrogen fuel cell trains?|maglev|"
    r"autonomous rail|automated trains?|civil supersonic|commercial supersonic|"
    r"quiet supersonic)\b",
    re.IGNORECASE,
)

LOW_QUALITY_AUTOMATED_PATTERN = re.compile(
    r"\b(?:stock|shares?|investors?|valuation|market size|market forecast|"
    r"market report|market landscape|market insights|market growth|cagr|"
    r"price target|buy or sell|top \d+|consumer list|game guide|personal injury "
    r"lawyer)\b",
    re.IGNORECASE,
)

SUBSTANTIVE_ACTION_PATTERN = re.compile(
    r"\b(?:announce[ds]?|approve[ds]?|authorize[ds]?|begin[ns]?|"
    r"deploy(?:s|ed|ing)?|expand(?:s|ed|ing)?|launch(?:es|ed|ing)?|"
    r"order[eds]?|permit(?:s|ted)?|propose[ds]?|support(?:s|ed|ing)?|"
    r"regulat(?:e[ds]?|ion)|rulemaking|test(?:s|ed|ing)?|trial(?:s|ed)?|"
    r"operate[ds]?|service|law|contract|investigation|recall)\b",
    re.IGNORECASE,
)

RECOGNIZED_EDITORIAL_SOURCE_PATTERN = re.compile(
    r"\b(?:associated press|reuters|washington post|new york times|"
    r"wall street journal|bloomberg|politico|axios|cnn|cnbc|bbc|"
    r"the guardian|financial times)\b",
    re.IGNORECASE,
)

EIPP_PROGRAM_PATTERN = re.compile(
    r"\b(?:eipp|e(?:lectric|vtol).{0,20}integration pilot program|"
    r"evtol integration pilot program)\b",
    re.IGNORECASE,
)

EIPP_FIRST_FLIGHT_PATTERN = re.compile(
    r"\b(?:(?:first|1st|inaugural).{0,180}(?:operational\s+)?flight|"
    r"(?:flight operations|operational flights?).{0,80}"
    r"(?:begin|began|commenc|launch|start)|"
    r"(?:begin|began|commenc|launch|start).{0,80}"
    r"(?:flight operations|operational flights?))",
    re.IGNORECASE,
)

FIFA_WORLD_CUP_PATTERN = re.compile(
    r"\b(?:fifa(?: world cup)?|world cup)\b",
    re.IGNORECASE,
)

FIFA_AIRSPACE_SECURITY_PATTERN = re.compile(
    r"\b(?:counter[- ](?:drone|uas)|c-uas|counter uas|drone detection|"
    r"drone mitigation|unauthorized drones?|rogue drones?|airspace security|"
    r"no drone zones?|temporary flight restrictions?|tfrs?)\b",
    re.IGNORECASE,
)

FIFA_SECURITY_ACTION_PATTERN = re.compile(
    r"\b(?:deploy(?:s|ed|ing)?|protect(?:s|ed|ing)?|secur(?:e[ds]?|ing)|"
    r"keep(?:s|ing)?|kept|retain(?:s|ed|ing)?|seiz(?:e[ds]?|ed|ing)|"
    r"enforc(?:e[ds]?|ed|ing)|fund(?:s|ed|ing)?|grant(?:s|ed)?|"
    r"equip(?:s|ped|ping)?|train(?:s|ed|ing)?|establish(?:es|ed|ing)?|"
    r"designat(?:e[ds]?|ed|ing)|implement(?:s|ed|ing)?|"
    r"use[ds]?|using|award(?:s|ed|ing)?)\b",
    re.IGNORECASE,
)

FIFA_SECURITY_NEGATIVE_PATTERN = re.compile(
    r"\b(?:unprepared|not ready|little progress|falls short|falling short|"
    r"behind schedule|security gap|security gaps)\b",
    re.IGNORECASE,
)

FIFA_SECURITY_LASTING_PATTERN = re.compile(
    r"\b(?:keep(?:s|ing)?|kept|retain(?:s|ed|ing)?|future|after the world cup|"
    r"concerts?|parades?|lasting|permanent)\b",
    re.IGNORECASE,
)

FAA_AGENCY_PATTERN = re.compile(
    r"\b(?:FAA|Federal Aviation Administration)\b",
    re.IGNORECASE,
)

FAA_STANDARDS_ACTION_PATTERN = re.compile(
    r"\b(?:accept(?:s|ed|ing)?|sign(?:s|ed)?\s+off|"
    r"announc(?:e[ds]?|ed|ing).{0,50}availability)\b",
    re.IGNORECASE,
)

FAA_MOSAIC_STANDARDS_PATTERN = re.compile(
    r"\b(?:MOSAIC|ASTM|consensus standards?|light-sport|powered[- ]lift)\b",
    re.IGNORECASE,
)

NHTSA_ROBOTAXI_RULE_ACTION_PATTERN = re.compile(
    r"\bNHTSA\b(?=.{0,220}\brobotaxi\b)"
    r"(?=.{0,220}\b(?:sets?|establish(?:es|ed)?|publish(?:es|ed)?|"
    r"finaliz(?:e[ds]?|ed|ing)|deadline|rulebook|final rule)\b)",
    re.IGNORECASE,
)

NHTSA_FMVSS_ACTION_PATTERN = re.compile(
    r"\bNHTSA\b(?=.{0,220}\bFMVSS\b)"
    r"(?=.{0,220}\b(?:ADS|autonomous|automated driving)\b)"
    r"(?=.{0,220}\b(?:moderniz(?:e[ds]?|ed|ing)|publish(?:es|ed)?|"
    r"finaliz(?:e[ds]?|ed|ing)|approv(?:e[ds]?|ed|ing)|final rule)\b)",
    re.IGNORECASE,
)

FEDERAL_AUTONOMOUS_F16_PATTERN = re.compile(
    r"(?=.*\bDARPA\b)(?=.*\b(?:U\.?S\.?\s+)?Air Force\b)"
    r"(?=.*\bF-?16\b)(?=.*\bautonomous\b)"
    r"(?=.*\b(?:fly|flies|flew|flown|flight)\b)",
    re.IGNORECASE,
)

PUBLISHER_ONLY_HEADLINES = {
    "aol",
    "associated press",
    "ap",
    "google news",
    "msn",
    "reuters",
    "yahoo",
    "yahoo finance",
}

GENERIC_SUPPLEMENTAL_HEADLINES = {
    "additional headline",
    "additional headlines",
    "imported from the supplemental daily news email",
    "supplemental daily news email",
    "supplemental story",
}


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


def normalized_headline_label(value: str) -> str:
    return clean_spaces(re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()))


def headline_is_publisher_only(value: str, source: str = "") -> bool:
    """Reject publisher labels and prohibited filler as story headlines."""
    label = normalized_headline_label(value)
    source_label = normalized_headline_label(source)
    if not label:
        return True
    return (
        label in PUBLISHER_ONLY_HEADLINES
        or label in GENERIC_SUPPLEMENTAL_HEADLINES
        or bool(source_label and label == source_label)
    )


def strip_html(value: str) -> str:
    return clean_spaces(html.unescape(re.sub(r"<[^>]+>", " ", value or "")))


def normalize_title(value: str) -> str:
    value = value.lower()
    value = re.sub(r"\s+-\s+[^-]{2,60}$", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return clean_spaces(value)


def distinct_story_summary(title: str, summary: str) -> str:
    """Return no summary when it merely repeats the headline."""
    summary = clean_spaces(summary)
    if not summary:
        return ""
    if normalize_title(summary) == normalize_title(title):
        return ""
    return summary


def canonical_eo_section(value: str) -> str:
    """Normalize common EO section spellings for display and lookup."""
    section = clean_spaces(value)
    match = re.fullmatch(
        r"(?:sec(?:tion)?\.?\s*)?(\d+)(?:\s*\(?([a-z])\)?)?",
        section,
        flags=re.IGNORECASE,
    )
    if not match:
        return section
    number, paragraph = match.groups()
    suffix = f"({paragraph.lower()})" if paragraph else ""
    return f"Section {number}{suffix}"


def eo_section_summary(eo_number: str, section: str) -> str:
    return EO_SECTION_SUMMARIES.get(eo_number, {}).get(
        canonical_eo_section(section),
        "",
    )


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
    positions: dict[tuple[str, str], list[int]] = {}
    unique: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda value: value["published"], reverse=True):
        key = (normalize_title(item["title"]), item.get("source", "").casefold())
        matches = positions.get(key, [])
        if not matches:
            positions[key] = [len(unique)]
            unique.append(item)
            continue

        if not item.get("required_include", False):
            continue

        same_url_index = next(
            (
                index
                for index in matches
                if unique[index].get("url", "") == item.get("url", "")
            ),
            None,
        )
        if same_url_index is not None:
            existing = unique[same_url_index]
            existing["required_include"] = True
            for field in (
                "pasted_headline",
                "pasted_context",
                "description",
            ):
                if item.get(field) and not existing.get(field):
                    existing[field] = item[field]
            continue

        # Required supplemental links must survive even when the publisher and
        # headline match another record; the AI may later identify them as true
        # same-event additional coverage.
        positions[key].append(len(unique))
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
            "win_explanation": {
                "type": "string",
                "description": (
                    "Reader-facing plain-English explanation naming the specific "
                    "Administration action and concrete American benefit; no internal "
                    "editorial-test language or vague procurement jargon."
                ),
            },
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
            "what_to_watch": {"type": "array", "items": {"type": "string"}},
            "clusters": {"type": "array", "items": cluster},
        },
        "required": ["what_to_watch", "clusters"],
        "additionalProperties": False,
    }


def executive_summary_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "executive_summary": {"type": "string"},
        },
        "required": ["executive_summary"],
        "additionalProperties": False,
    }


def best_record_title(record: dict[str, Any]) -> str:
    candidates = [
        record.get("editor_title_override", ""),
        record.get("original_title", ""),
        record.get("title", ""),
        record.get("pasted_headline", ""),
        record.get("pasted_context", ""),
    ]
    source = clean_spaces(record.get("source", ""))
    for value in candidates:
        value = clean_spaces(value)
        if not value:
            continue
        if source:
            value = re.sub(
                rf"\s*[-|–—]\s*{re.escape(source)}\s*$",
                "",
                value,
                flags=re.IGNORECASE,
            ).strip()
        if headline_is_publisher_only(value, source):
            continue
        if value.lower().startswith(("http://", "https://")):
            continue
        if len(value) >= 10:
            return value[:240]
    return clean_spaces(record.get("url", "Untitled supplemental item"))


def record_text(record: dict[str, Any]) -> str:
    return clean_spaces(
        " ".join(
            str(record.get(key, ""))
            for key in (
                "title", "summary", "description", "pasted_context",
                "search_section", "source",
            )
        )
    )


def record_content_text(record: dict[str, Any]) -> str:
    """Reader-facing record text, excluding search-bucket metadata."""
    return clean_spaces(
        " ".join(
            str(record.get(key, ""))
            for key in (
                "title", "summary", "description", "pasted_context", "source",
            )
        )
    )


def record_is_military(record: dict[str, Any]) -> bool:
    text = record_text(record)
    return bool(
        MILITARY_SECTION_PATTERN.search(text)
        or (
            MILITARY_CONFLICT_ACTOR_PATTERN.search(text)
            and MILITARY_CONFLICT_CONTEXT_PATTERN.search(text)
        )
    )


def record_is_international(record: dict[str, Any]) -> bool:
    title = clean_spaces(record.get("title", ""))
    if (
        INTERNATIONAL_MARKER_PATTERN.search(title)
        and not DOMESTIC_MARKER_PATTERN.search(title)
    ):
        return True

    text = record_content_text(record)
    if DOMESTIC_MARKER_PATTERN.search(text):
        return False
    return bool(
        INTERNATIONAL_MARKER_PATTERN.search(text)
        or (
            record.get("search_section") == "International"
            and INTERNATIONAL_TRANSPORT_PATTERN.search(text)
        )
    )


def infer_section(record: dict[str, Any]) -> str:
    text = record_text(record).casefold()
    if record_is_military(record):
        return "Military"
    if record_is_international(record):
        return "International"
    if any(term in text for term in (
        "counter-uas", "counter uas", "c-uas", "counter drone", "drone detection",
        "drone mitigation", "airspace sovereignty", "unauthorized drone",
    )):
        return "UAS Security and C-UAS"
    if any(term in text for term in (
        "evtol", "eipp", "advanced air mobility", "air taxi", "powered-lift",
        "powered lift", "vertiport", "electric aircraft",
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


def recognized_administration_win(
    record: dict[str, Any],
) -> dict[str, str] | None:
    """Identify narrow, well-documented implementation wins the AI may miss."""
    text = record_content_text(record)
    title = clean_spaces(record.get("title", ""))
    if (
        not title
        or headline_is_publisher_only(title, record.get("source", ""))
        or LOW_QUALITY_AUTOMATED_PATTERN.search(title)
    ):
        return None

    if EIPP_PROGRAM_PATTERN.search(text) and EIPP_FIRST_FLIGHT_PATTERN.search(text):
        return {
            "event_key": "eipp-first-operational-flight",
            "eo_number": "EO 14307",
            "eo_section": "Section 6",
            "win_explanation": (
                "President Trump turned his Unleashing American Drone Dominance "
                "order into real-world results as DOT and FAA’s eIPP completed its "
                "first operational flight. This huge America-first win strengthens "
                "U.S. leadership in next-generation aviation, supports high-skilled "
                "jobs, and accelerates safer medical, cargo, and passenger "
                "transportation for American communities."
            ),
        }

    if (
        FAA_AGENCY_PATTERN.search(text)
        and FAA_STANDARDS_ACTION_PATTERN.search(text)
        and FAA_MOSAIC_STANDARDS_PATTERN.search(text)
    ):
        return {
            "event_key": "faa-mosaic-accepted-standards",
            "eo_number": "",
            "eo_section": "",
            "win_explanation": (
                "President Trump’s FAA accepted new industry standards for "
                "light-sport aircraft, including powered-lift designs, turning the "
                "Administration’s pro-innovation aviation agenda into practical "
                "results. This huge win gives American manufacturers and pilots a "
                "clearer path to bring safer, more capable aircraft to market while "
                "strengthening U.S. leadership in general aviation."
            ),
        }

    if NHTSA_ROBOTAXI_RULE_ACTION_PATTERN.search(text):
        return {
            "event_key": "",
            "eo_number": "",
            "eo_section": "",
            "win_explanation": (
                "President Trump’s NHTSA set a firm federal path for the next "
                "robotaxi safety rulebook, giving American innovators clearer rules "
                "while putting public safety first. This huge win helps the "
                "United States lead the driverless-vehicle revolution, supports "
                "high-tech American jobs, and advances safer transportation for "
                "families and communities."
            ),
        }

    if NHTSA_FMVSS_ACTION_PATTERN.search(text):
        return {
            "event_key": "",
            "eo_number": "",
            "eo_section": "",
            "win_explanation": (
                "President Trump’s NHTSA modernized Federal Motor Vehicle Safety "
                "Standards for automated-driving vehicles, replacing outdated "
                "requirements with a clearer path for American innovation. This "
                "huge win puts safety first, supports high-tech American jobs, and "
                "helps the United States lead the world in responsible "
                "driverless-vehicle deployment."
            ),
        }

    if FEDERAL_AUTONOMOUS_F16_PATTERN.search(text):
        return {
            "event_key": "darpa-air-force-autonomous-f16-flight",
            "eo_number": "",
            "eo_section": "",
            "win_explanation": (
                "Under President Trump, DARPA and the U.S. Air Force flew a "
                "frontline F-16 modified for autonomous flight, moving American "
                "military aviation technology from the laboratory into the air. "
                "This huge win strengthens U.S. technological leadership, "
                "gives American warfighters a decisive edge, and reinforces the "
                "deterrence that keeps the Nation secure."
            ),
        }

    if (
        not record_is_international(record)
        and FIFA_WORLD_CUP_PATTERN.search(text)
        and FIFA_AIRSPACE_SECURITY_PATTERN.search(text)
        and FIFA_SECURITY_ACTION_PATTERN.search(text)
        and not FIFA_SECURITY_NEGATIVE_PATTERN.search(text)
    ):
        if FIFA_SECURITY_LASTING_PATTERN.search(text):
            explanation = (
                "President Trump’s Restoring American Airspace Sovereignty agenda "
                "delivered a huge, lasting win for the American people: counter-drone "
                "technology deployed for the FIFA World Cup will remain available to "
                "local law enforcement for future mass gatherings. America gains "
                "safer public events and stronger local defenses against dangerous "
                "or unlawful drones."
            )
        else:
            explanation = (
                "President Trump’s Restoring American Airspace Sovereignty agenda "
                "put counter-drone protections to work for the FIFA World Cup, "
                "safeguarding American families and visitors at one of the world’s "
                "largest sporting events. This huge win strengthens U.S. control of "
                "its skies and builds lasting federal and local capacity to defeat "
                "rogue-drone threats."
            )
        return {
            "event_key": "",
            "eo_number": "EO 14305",
            "eo_section": "Section 9",
            "win_explanation": explanation,
        }

    return None


def ensure_recognized_administration_wins(
    clusters: list[dict[str, Any]],
    articles: list[dict[str, Any]],
) -> list[str]:
    """Apply narrow EO implementation wins even when the AI omits their flags."""
    recognized_ids: list[str] = []
    article_lookup = {item["id"]: item for item in articles}
    recognized_event_clusters: dict[str, dict[str, Any]] = {}
    for record in articles:
        override = recognized_administration_win(record)
        if not override:
            continue

        event_key = override.get("event_key", "")
        existing = next(
            (
                cluster
                for cluster in clusters
                if record["id"] in cluster.get("article_ids", [])
            ),
            None,
        )
        event_cluster = (
            recognized_event_clusters.get(event_key) if event_key else None
        )
        if existing is not None and event_cluster is not None and existing is not event_cluster:
            for article_id in existing.get("article_ids", []):
                if article_id not in event_cluster["article_ids"]:
                    event_cluster["article_ids"].append(article_id)
            clusters.remove(existing)
            existing = event_cluster
        elif existing is None and event_cluster is not None:
            existing = event_cluster

        if existing is None:
            title = best_record_title(record)
            summary = distinct_story_summary(
                title,
                clean_spaces(
                    record.get("description", "")
                    or record.get("summary", "")
                    or record.get("pasted_context", "")
                ),
            )
            existing = {
                "cluster_id": stable_id(
                    "recognized-administration-win",
                    record["id"],
                ),
                "article_ids": [record["id"]],
                "primary_article_id": record["id"],
                "section": infer_section(record),
                "relevant": True,
                "importance": 9,
                "canonical_title": title,
                "summary": summary[:500],
                "is_administration_win": True,
                "eo_number": override["eo_number"],
                "eo_section": override["eo_section"],
                "win_explanation": override["win_explanation"],
                "confidence": "high",
                "exclude_reason": "",
            }
            clusters.append(existing)
        else:
            if record["id"] not in existing["article_ids"]:
                existing["article_ids"].append(record["id"])
            current_primary = article_lookup.get(existing["primary_article_id"], {})
            if (
                record.get("origin") == "Federal Register API"
                and current_primary.get("origin") != "Federal Register API"
            ):
                existing["primary_article_id"] = record["id"]
                existing["canonical_title"] = best_record_title(record)
                existing["summary"] = distinct_story_summary(
                    existing["canonical_title"],
                    clean_spaces(
                        record.get("description", "")
                        or record.get("summary", "")
                        or record.get("pasted_context", "")
                    ),
                )[:500]
            existing.update(
                {
                    "section": infer_section(record),
                    "relevant": True,
                    "importance": max(9, existing.get("importance", 1)),
                    "is_administration_win": True,
                    "eo_number": override["eo_number"],
                    "eo_section": override["eo_section"],
                    "win_explanation": override["win_explanation"],
                    "confidence": "high",
                    "exclude_reason": "",
                }
            )
        if event_key:
            recognized_event_clusters[event_key] = existing
        recognized_ids.append(record["id"])
    return recognized_ids


def coverage_floor_score(
    section: str,
    record: dict[str, Any],
) -> int | None:
    """Score a credible record for deterministic minimum sector coverage."""
    title = clean_spaces(record.get("title", ""))
    text = record_content_text(record)
    if (
        not title
        or headline_is_publisher_only(title, record.get("source", ""))
        or LOW_QUALITY_AUTOMATED_PATTERN.search(title)
        or record_is_military(record)
    ):
        return None

    is_international = record_is_international(record)
    if section == "International":
        if not is_international or not INTERNATIONAL_TRANSPORT_PATTERN.search(text):
            return None
    else:
        pattern = SECTION_COVERAGE_PATTERNS[section]
        if is_international or not pattern.search(text):
            return None

    score = 4 if record.get("search_section") == section else 0
    if SUBSTANTIVE_ACTION_PATTERN.search(text):
        score += 4
    if DOMESTIC_MARKER_PATTERN.search(text):
        score += 3
    if any(
        agency in record.get("source", "").casefold()
        for agency in (
            "department of transportation", "nhtsa", "federal railroad",
            "federal transit", "faa", "amtrak",
        )
    ):
        score += 4
    if RECOGNIZED_EDITORIAL_SOURCE_PATTERN.search(record.get("source", "")):
        score += 3
    if title.endswith("?"):
        score -= 1
    return score


def best_coverage_floor_record(
    section: str,
    articles: list[dict[str, Any]],
) -> dict[str, Any] | None:
    candidates = [
        (score, record.get("published", ""), record)
        for record in articles
        if (score := coverage_floor_score(section, record)) is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def ensure_minimum_section_coverage(
    clusters: list[dict[str, Any]],
    articles: list[dict[str, Any]],
) -> list[str]:
    """Keep credible AV, advanced-transportation, and international news visible."""
    ensured: list[str] = []
    for section in COVERAGE_FLOOR_SECTIONS:
        if any(
            cluster.get("relevant", False) and cluster.get("section") == section
            for cluster in clusters
        ):
            continue

        record = best_coverage_floor_record(section, articles)
        if not record:
            continue

        existing = next(
            (
                cluster
                for cluster in clusters
                if record["id"] in cluster.get("article_ids", [])
            ),
            None,
        )
        if existing:
            existing["relevant"] = True
            existing["section"] = section
            existing["importance"] = max(5, existing.get("importance", 1))
            existing["exclude_reason"] = ""
        else:
            title = best_record_title(record)
            summary = distinct_story_summary(
                title,
                clean_spaces(
                    record.get("description", "")
                    or record.get("summary", "")
                ),
            )
            clusters.append(
                {
                    "cluster_id": stable_id("coverage-floor", section, record["id"]),
                    "article_ids": [record["id"]],
                    "primary_article_id": record["id"],
                    "section": section,
                    "relevant": True,
                    "importance": 5,
                    "canonical_title": title,
                    "summary": summary[:500],
                    "is_administration_win": False,
                    "eo_number": "",
                    "eo_section": "",
                    "win_explanation": "",
                    "confidence": "medium",
                    "exclude_reason": "",
                }
            )
        ensured.append(section)
    return ensured


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
            "editor_title_override": item.get("editor_title_override", ""),
            "summary": item.get("summary", ""),
            "description": item.get("description", ""),
            "pasted_headline": item.get("pasted_headline", ""),
            "pasted_context": item.get("pasted_context", ""),
            "source": item.get("source", ""),
            "published": item.get("published", ""),
            "origin": item.get("origin", ""),
            "required_include": bool(item.get("required_include", False)),
            "editor_vetted": bool(item.get("editor_vetted", False)),
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
- Supplemental records are editor-vetted. Presume every record with
  required_include=true is relevant and included; do not subject it to the automated-feed
  relevance exclusions or omit it because it seems low importance.
- Every record with required_include=true MUST be accounted for.
- A required item must either be the primary article in a distinct story or appear in
  article_ids as genuine same-event coverage of another story.
- Never discard a required item merely because it is low importance or imperfectly formatted.
- Never use a publisher name such as "MSN", "AOL", or "Yahoo" as the canonical headline.
- Use the pasted headline, surrounding context, fetched metadata, and URL information to
  write a specific factual headline.
- Never use generic filler such as "Imported from the supplemental daily news email."
- Different events must remain separate stories. Merge only true same-event coverage.
- If there is any doubt whether a required item describes the same concrete event as an
  automated item, keep the required item as a distinct story.

EDITORIAL SCOPE AND RELEVANCE
- Produce a useful, fairly comprehensive briefing on:
  1. UAS and drones.
  2. UAS security and counter-UAS.
  3. Military applications, procurement, operations, testing, and defense technology.
  4. eIPP, eVTOL, powered-lift, and advanced air mobility.
  5. Autonomous vehicles and automated driving.
  6. Civil supersonics and genuinely advanced rail or transportation technology.
  7. International advanced-transportation developments.
  8. Directly relevant Federal actions.
- Put every military-related story in Military, regardless of whether its platform is a drone,
  ship, aircraft, ground vehicle, missile, or autonomous system. This includes the Department
  of Defense, military services, defense acquisition and contractors, bases, warfighters,
  weapons and munitions, military tests and exercises, battlefield or naval operations, combat
  strikes, and reporting about the war in Ukraine or Russian military operations. Do not put
  these stories in generic UAS, UAS Security and C-UAS, or Federal Actions.
- Autonomous Vehicles explicitly includes robotaxis, privately owned automated vehicles,
  autonomous trucking, ADS-equipped commercial motor vehicles, NHTSA and FMCSA actions,
  FMVSS modernization, Part 555 exemptions, recalls, investigations, permits, state and
  local laws, deployments, testing, simulation, mapping, and V2X when directly connected
  to automated driving.
- Place AV-specific Federal actions, including FMVSS and NHTSA/FMCSA ADS actions, in the
  Autonomous Vehicles section rather than the generic Federal Actions section.
- Put substantive non-U.S. commercial, regulatory, operational, and technical
  developments in International. This includes foreign drone, eVTOL, AV, advanced-rail,
  and civil-supersonic news. International military and conflict stories still belong in
  Military. Do not move a U.S.-centered story into International merely because it mentions
  a foreign company, comparison, supplier, or market.
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

MINIMUM SECTOR COVERAGE
- Review the full candidate set. Do not let a large volume of UAS, military, or supplemental
  material crowd out other advanced-transportation sectors.
- An editorial section may be empty only when the supplied records contain no substantive,
  credible new development for it.
- When credible candidates exist, include at least the strongest new Autonomous Vehicles
  story, the strongest new Other Advanced Transportation story, and the strongest new
  International story. Prefer concrete deployments, government actions, safety developments,
  tests, contracts, launches, and operational milestones.
- This is not permission to include stock promotion, generic market reports, consumer lists,
  unrelated keyword collisions, or stale recaps merely to fill a section.

CLUSTERING
- Cluster only records covering the same concrete event, announcement, rule, deployment,
  flight, contract, facility, study, or government action.
- Different companies, cities, contracts, tests, rules, or deployments are separate stories.
- Most clusters should contain 1-4 records.
- Do not create broad umbrella stories such as "drone activity expands" or "market activity grows."

HEADLINES AND SUMMARIES
- For each story, use the actual headline of the selected primary article. Use that
  record's editor_title_override when present. Otherwise use original_title verbatim
  when it is available and usable, removing only a publisher suffix; then use title.
- Never substitute a quotation, description, summary fragment, or surrounding email prose
  for an article headline. Do not invent or paraphrase a new headline.
- Write 1-2 concise factual sentences, normally 35-70 words.
- If the available description or summary merely repeats the headline, return an empty summary.
- Select the strongest primary source using this preference: {SOURCE_PREFERENCE}
- Preserve every required supplemental URL either as primary coverage or "Also covered by."

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
- win_direct_administration_nexus=true when the record supports the nexus. A new action
  by a current executive-branch department, agency, military service, or Federal program
  is itself a direct Trump Administration action; the article does not also have to use
  President Trump's name. A private, State, local, congressional, judicial, or foreign
  action does not gain that nexus merely because it aligns with Administration policy.
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

HIGH-PRIORITY EO IMPLEMENTATION WINS
- Do not overlook the first or inaugural operational flight under DOT and FAA's eIPP.
  It is direct implementation of President Trump's EO 14307, Section 6, and should be
  presented as a major America-first win for U.S. aviation leadership, high-skilled jobs,
  useful transportation services, and faster commercialization of advanced aircraft.
- Do not overlook a current U.S. FIFA World Cup counter-UAS deployment, enforcement
  action, security operation, federally backed capability, or decision to retain that
  equipment for future public events. It is direct implementation of President Trump's
  EO 14305, Section 9, which explicitly builds Federal and SLTT counter-UAS capacity for
  the FIFA World Cup and other major sporting events.
- These examples still require a real implementation action, flight, deployment,
  enforcement result, or lasting operational capability. Generic eIPP explainers,
  unrelated FIFA coverage, security criticism, and private promotion are not wins.
- Also recognize concrete current actions such as FAA acceptance of aviation standards,
  a NHTSA automated-driving rule or firm rulemaking milestone, and a DARPA or U.S. military
  operational technology flight or test when the other gates are supported. Do not reduce
  these to generic agency news merely because the headline omits President Trump's name.

- A positive private-sector story is not automatically an Administration win.
- When applicable, use exactly EO 14307, EO 14305, or EO 14304 and identify the section using
  the form "Section 3" or "Section 4(a)" so the app can add its plain-English section summary.
- Write one 30-55 word explanation that will be published verbatim in the email.
- Make the explanation stand on its own for a reader who has not seen these instructions:
  name the Administration, agency, or federal program that acted; state what it actually
  did; and explain the specific benefit for U.S. capability, jobs, manufacturing, safety,
  security, deployment, or regulatory progress.
- Use a strongly pro-Trump, pro-America voice for a supported Win. Confidently credit
  President Trump's leadership, describe why the action is a major win for the American
  people, and connect the result to safer communities, American jobs and innovation,
  national leadership, or control of U.S. airspace. Keep every factual claim supportable.
- Use active voice and ordinary language. If the event is a contract or purchase, say who
  awarded or ordered what, who will provide it, and what American mission it supports.
- Never say "during the window," "within the coverage window," "the record shows,"
  "qualifies as a win," "clear federal procurement action," "direct nexus," "concrete
  benefit," or similar internal editorial language. Do not tell the reader that the story
  passed a test; explain the real-world action and result instead.
- The app displays the full EO name separately, so do not begin "This is a win for EO...".

WHAT TO WATCH
- Return 0-3 concise, supportable next steps, deadlines, or unresolved developments.

Do not write an Executive Summary in this pass. First complete the story selection,
clustering, section assignments, story summaries, Win determinations, and What to Watch.
The Executive Summary will be written in a separate final pass from that compiled briefing.

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


def request_structured_output(
    messages: list[dict[str, str]],
    api_key: str,
    model: str,
    schema_name: str,
    schema: dict[str, Any],
    max_output_tokens: int,
) -> tuple[dict[str, Any], dict[str, Any], float | None]:
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing.")

    payload = {
        "model": model,
        "input": messages,
        "reasoning": {"effort": "none"},
        "max_output_tokens": max_output_tokens,
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": schema,
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


def analyze_articles(
    articles: list[dict[str, Any]],
    api_key: str,
    model: str,
    window_start: datetime,
    window_end: datetime,
) -> tuple[dict[str, Any], dict[str, Any], float | None]:
    return request_structured_output(
        prompt_messages(articles, window_start, window_end),
        api_key,
        model,
        "transportation_news_analysis",
        analysis_schema(),
        20000,
    )


def administration_win_is_eligible(raw: dict[str, Any]) -> bool:
    """Apply the four mandatory Administration Win eligibility gates."""
    return (
        bool(raw.get("is_administration_win", False))
        and bool(raw.get("win_event_within_window", False))
        and bool(raw.get("win_direct_administration_nexus", False))
        and bool(raw.get("win_concrete_american_benefit", False))
        and not bool(raw.get("win_foreign_company_expansion_only", False))
    )


def executive_summary_sentence_is_public(sentence: str) -> bool:
    lowered = sentence.casefold()
    if any(marker in lowered for marker in EXECUTIVE_SUMMARY_PROCESS_MARKERS):
        return False
    mentions_win = bool(re.search(r"\bwins?\b", lowered))
    mentions_administration = bool(
        re.search(r"\badministrat(?:ion|ive)\b", lowered)
    )
    return not (mentions_win and mentions_administration)


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
        inferred_section = infer_section(lookup[primary])
        if inferred_section in {
            "Military",
            "International",
            "Autonomous Vehicles",
            "Other Advanced Transportation",
        }:
            section = inferred_section
        elif section not in TOPIC_SECTIONS or (
            section == "Federal Actions"
            and inferred_section == "Autonomous Vehicles"
        ):
            section = inferred_section

        eo_number = clean_spaces(raw.get("eo_number", ""))
        if eo_number not in EO_DISPLAY_NAMES:
            eo_number = ""

        includes_required = any(article_id in required for article_id in ids)

        validated_win = administration_win_is_eligible(raw)

        if not validated_win:
            eo_number = ""
            eo_section = ""
            win_explanation = ""
        else:
            eo_section = canonical_eo_section(raw.get("eo_section", ""))
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
        description = distinct_story_summary(title, description)
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

    recognized_win_ids = ensure_recognized_administration_wins(
        clusters,
        articles,
    )
    coverage_floor_sections = ensure_minimum_section_coverage(clusters, articles)

    return {
        "what_to_watch": [
            clean_spaces(item)
            for item in analysis.get("what_to_watch", [])[:3]
            if clean_spaces(item)
        ],
        "clusters": clusters,
        "required_count": len(required),
        "required_accounted_count": len(required),
        "recognized_administration_win_ids": recognized_win_ids,
        "coverage_floor_sections": coverage_floor_sections,
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

    # Headlines are source metadata, not AI-authored copy. This prevents a
    # description, quotation, or model paraphrase from replacing the article title.
    title = best_record_title(primary)
    summary = cluster["summary"] or clean_spaces(
        primary.get("description", "")
        or primary.get("summary", "")
        or primary.get("pasted_context", "")
    )
    summary = distinct_story_summary(title, summary)

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
        "eo_section_summary": eo_section_summary(
            cluster["eo_number"], cluster["eo_section"]
        ),
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
    sections["Trump Administration Wins"] = wins
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


def represented_urls(sections: dict[str, list[dict[str, Any]]]) -> set[str]:
    urls: set[str] = set()
    for items in sections.values():
        for item in items:
            if item.get("url"):
                urls.add(item["url"])
            urls.update(
                related["url"]
                for related in item.get("also_covered", [])
                if related.get("url")
            )
    return urls


def supplemental_link_accounting(
    supplemental_records: list[dict[str, Any]],
    sections: dict[str, list[dict[str, Any]]],
) -> tuple[int, int]:
    supplemental_urls = {
        item.get("url", "") for item in supplemental_records if item.get("url")
    }
    included_urls = represented_urls(sections)
    return len(supplemental_urls), len(supplemental_urls & included_urls)


def executive_summary_messages(
    sections: dict[str, list[dict[str, Any]]],
    what_to_watch: list[str],
    regulatory_tracker: list[dict[str, Any]],
    window_start: datetime,
    window_end: datetime,
) -> list[dict[str, str]]:
    """Build the final-pass prompt from compiled reader-facing material only."""
    stories: list[dict[str, str]] = []
    seen: set[str] = set()
    for display_section in SECTION_ORDER:
        for item in sections.get(display_section, []):
            identity = item.get("id") or item.get("url") or item.get("title", "")
            if identity in seen:
                continue
            seen.add(identity)
            stories.append(
                {
                    "topic": item.get("section", ""),
                    "headline": item.get("title", ""),
                    "summary": item.get("summary", ""),
                    "source": item.get("source", ""),
                    "date": item.get("date_label", ""),
                }
            )

    tracker_context = [
        {
            "agency": item.get("agency", ""),
            "action": item.get("action", ""),
            "comment_deadline": item.get("comment_deadline_label", ""),
            "status": item.get("status", ""),
        }
        for item in regulatory_tracker
    ]
    compiled = {
        "coverage_window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
        },
        "final_stories": stories,
        "what_to_watch": what_to_watch,
        "regulatory_tracker_context": tracker_context,
    }
    developer = """
You are writing the Executive Summary as the final step of a completed daily
U.S. advanced-transportation news briefing. The material supplied below is the
final, authoritative briefing; base the summary only on those reader-facing
facts.

- Write a polished, standalone briefing for a senior executive in 2-3 sentences
  and 60-100 words.
- Lead with the most consequential developments and accurately describe the
  day's overall pattern. Do not turn a minor item into the lead.
- Do not introduce facts, causal claims, credit, or conclusions that do not
  appear in the compiled stories.
- Do not mention records, links, intake methods, supplemental or automated
  material, accounting, editorial workflow, prompts, sections, or how the
  briefing was assembled.
- Do not discuss whether any item is or is not an Administration Win, or why it
  passed or failed any eligibility test. Report the underlying news only.
- The regulatory tracker is background context. Do not elevate a standing
  deadline into daily news unless the same development appears in final_stories.
""".strip()
    user = "Write the final Executive Summary from this compiled briefing:\n" + json.dumps(
        compiled, ensure_ascii=False
    )
    return [
        {"role": "developer", "content": developer},
        {"role": "user", "content": user},
    ]


def sanitize_compiled_executive_summary(
    value: str,
    sections: dict[str, list[dict[str, Any]]],
) -> str:
    """Keep final summary copy public-facing and derive a factual fallback."""
    sentences = re.split(r"(?<=[.!?])\s+", clean_spaces(value))
    public_sentences = [
        sentence
        for sentence in sentences
        if sentence and executive_summary_sentence_is_public(sentence)
    ]
    if public_sentences:
        return clean_spaces(" ".join(public_sentences))

    titles: list[str] = []
    seen: set[str] = set()
    for section in SECTION_ORDER:
        for item in sections.get(section, []):
            title = clean_spaces(item.get("title", ""))
            if title and title.casefold() not in seen:
                seen.add(title.casefold())
                titles.append(title)
            if len(titles) == 3:
                break
        if len(titles) == 3:
            break
    if not titles:
        return "Today's briefing covers the most consequential developments in advanced transportation."
    if len(titles) == 1:
        return f"The leading development is {titles[0]}."
    if len(titles) == 2:
        return f"Key developments include {titles[0]} and {titles[1]}."
    return f"Key developments include {titles[0]}; {titles[1]}; and {titles[2]}."


def generate_final_executive_summary(
    sections: dict[str, list[dict[str, Any]]],
    what_to_watch: list[str],
    regulatory_tracker: list[dict[str, Any]],
    api_key: str,
    model: str,
    window_start: datetime,
    window_end: datetime,
) -> tuple[str, dict[str, Any], float | None]:
    result, usage, cost = request_structured_output(
        executive_summary_messages(
            sections,
            what_to_watch,
            regulatory_tracker,
            window_start,
            window_end,
        ),
        api_key,
        model,
        "transportation_news_executive_summary",
        executive_summary_schema(),
        1200,
    )
    return (
        sanitize_compiled_executive_summary(
            result.get("executive_summary", ""), sections
        ),
        usage,
        cost,
    )


def combine_usage(*passes: dict[str, Any]) -> dict[str, int]:
    return {
        key: sum(int(usage.get(key, 0) or 0) for usage in passes)
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }


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
        record["editor_vetted"] = True
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
    raw_analysis, analysis_usage, analysis_cost = analyze_articles(
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
    supplemental_count, supplemental_accounted_count = (
        supplemental_link_accounting(supplemental, arranged)
    )
    regulatory_tracker = build_regulatory_tracker(end)

    # The Executive Summary is deliberately the final AI step. It sees the
    # arranged, reader-facing briefing rather than raw intake records or Win gates.
    executive_summary, summary_usage, summary_cost = generate_final_executive_summary(
        arranged,
        analysis["what_to_watch"],
        regulatory_tracker,
        api_key,
        model,
        start,
        end,
    )
    usage = combine_usage(analysis_usage, summary_usage)
    cost = (
        analysis_cost + summary_cost
        if analysis_cost is not None and summary_cost is not None
        else None
    )

    return {
        "generated_at": datetime.now(EASTERN).isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "model": model,
        "usage": usage,
        "estimated_cost": cost,
        "executive_summary": executive_summary,
        "what_to_watch": analysis["what_to_watch"],
        "regulatory_tracker": regulatory_tracker,
        "sections": arranged,
        "source_errors": raw_feed.get("source_errors", []),
        "candidate_count": len(combined),
        "raw_automated_candidate_count": len(raw_automated),
        "automated_candidate_count": len(automated),
        "automated_filtered_out_count": len(raw_automated) - len(automated),
        "supplemental_count": supplemental_count,
        "supplemental_accounted_count": supplemental_accounted_count,
        "candidate_counts": raw_feed.get("candidate_counts", {}),
        "included_counts": included_counts,
        "recognized_administration_win_ids": (
            analysis["recognized_administration_win_ids"]
        ),
        "coverage_floor_sections": analysis["coverage_floor_sections"],
    }
