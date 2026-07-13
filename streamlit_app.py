import hashlib
import hmac
import html
import json
import random
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import feedparser
import requests
import streamlit as st
import streamlit.components.v1 as components


st.set_page_config(
    page_title="News Update",
    page_icon="📰",
    layout="wide",
    initial_sidebar_state="collapsed",
)

EASTERN = ZoneInfo("America/New_York")
LOOKBACK_DAYS = 2
ITEMS_PER_SECTION = 10
OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
DEFAULT_OPENAI_FALLBACK_MODELS = [
    "gpt-5-mini",
]
TRANSIENT_OPENAI_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}

# Standard token prices per 1 million tokens. These are used only to
# display an approximate cost after a run; OpenAI billing is authoritative.
OPENAI_TOKEN_PRICES = {
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
    "gpt-5-mini": {"input": 0.25, "output": 2.00},
}

TOPIC_SECTIONS = [
    "UAS and Drones",
    "UAS Security",
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
    "Trump Administration Wins": (
        '("Unleashing American Drone Dominance" OR '
        '"Restoring American Airspace Sovereignty" OR '
        '"Leading the World in Supersonic Flight" OR '
        '"eVTOL Integration Pilot Program")'
    ),
    "UAS and Drones": (
        '("unmanned aircraft system" OR "uncrewed aircraft system" OR '
        '"beyond visual line of sight" OR BVLOS OR "drone delivery" OR '
        '"Part 107" OR "Remote ID")'
    ),
    "UAS Security": (
        '("counter-UAS" OR "counter drone" OR "drone incursion" OR '
        '"unauthorized drone" OR "drone detection" OR '
        '"airspace sovereignty" OR "Section 2209")'
    ),
    "eVTOL Integration Pilot Program and AAM": (
        '("eVTOL Integration Pilot Program" OR eIPP OR '
        '"advanced air mobility" OR powered-lift OR air-taxi)'
    ),
    "Autonomous Vehicles": (
        '("autonomous vehicle" OR robotaxi OR "automated driving system" OR '
        '"self-driving vehicle" OR "automated vehicle") '
        '(NHTSA OR DOT OR United States OR American)'
    ),
    "Other Advanced Transportation": (
        '("civil supersonic" OR "quiet supersonic" OR '
        '"overland supersonic" OR "high-speed rail" OR maglev OR '
        '"autonomous rail" OR "advanced transportation")'
    ),
}

FEDERAL_REGISTER_TERMS = [
    "unmanned aircraft",
    "drone",
    "advanced air mobility",
    "autonomous vehicle",
    "automated driving",
    "supersonic",
    "high-speed rail",
]

HEADERS = {
    "User-Agent": (
        "TransportationNewsUpdate/2.0 "
        "(public-source Streamlit briefing; contact via repository)"
    )
}

EO_REFERENCE = """
AUTHORITATIVE EXECUTIVE-ORDER REFERENCE FOR CLASSIFICATION

EO 14307 — Unleashing American Drone Dominance (June 6, 2025)
- Sec. 3: Continue American leadership in UAS development, commercialization, export, routine advanced operations, domestic commercialization, streamlined approvals, and the trusted American drone industrial base.
- Sec. 4(a): FAA proposed and final rulemaking to enable routine BVLOS operations.
- Sec. 4(c): FAA deployment of AI tools to help expedite Part 107 waiver reviews.
- Sec. 5(a): Updated civil-UAS integration roadmap.
- Sec. 5(b): Full use of FAA UAS Test Ranges for BVLOS, autonomy, AAM, testing, scaling, and rulemaking data.
- Sec. 6: Establish the eVTOL Integration Pilot Program (eIPP) to accelerate safe and lawful eVTOL deployment in the United States and use program experience to inform regulation and planning.
- Sec. 7: Strengthen the American drone industrial base, prioritize U.S.-manufactured UAS, protect supply chains, and reduce foreign dependence.

EO 14305 — Restoring American Airspace Sovereignty (June 6, 2025)
- Sec. 3: Protect the public, critical infrastructure, mass gatherings, military sites, and sensitive government operations from careless or unlawful UAS use.
- Sec. 4: Federal Task Force to Restore American Airspace Sovereignty.
- Sec. 5: Section 2209 fixed-site restriction rulemaking, security coordination, and open-format NOTAM/TFR information for geofencing and navigation systems.
- Sec. 8: Assess expanded protections for borders, airports, Federal facilities, critical infrastructure, and military assets.
- Sec. 9: Build counter-UAS capacity, operational coordination, a national training center, and Federal/SLTT capabilities.

EO 14304 — Leading the World in Supersonic Flight (June 6, 2025)
- Sec. 2: Remove obsolete regulatory barriers to civil supersonic flight and establish noise-based certification standards.
- Sec. 3: OSTP-led Federal coordination of supersonic R&D, testing, regulatory data, commercial viability, and operational integration.
- Sec. 4: International engagement and alignment on civil-supersonic regulation and safety agreements.
""".strip()

EO_DISPLAY_NAMES = {
    "EO 14307": "Unleashing American Drone Dominance",
    "14307": "Unleashing American Drone Dominance",
    "EO 14305": "Restoring American Airspace Sovereignty",
    "14305": "Restoring American Airspace Sovereignty",
    "EO 14304": "Leading the World in Supersonic Flight",
    "14304": "Leading the World in Supersonic Flight",
}

SOURCE_PREFERENCE = """
Source preference, strongest first: official White House/Federal agency/Federal Register;
original program or company announcement; Reuters/AP/major national outlet; recognized
aviation, transportation, security, or technology trade publication; credible local outlet;
aggregator. Stock-promotion articles, generic market commentary, product lists, celebrity
commentary, and unrelated keyword collisions should normally be excluded.
""".strip()


def clean_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def strip_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return clean_spaces(html.unescape(value))


def parse_rss_date(value: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(EASTERN)
    except (TypeError, ValueError, OverflowError):
        return datetime.now(EASTERN)


def normalize_title(value: str) -> str:
    value = value.lower()
    value = re.sub(r"\s+-\s+[^-]{2,60}$", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return clean_spaces(value)


def stable_id(*parts: str) -> str:
    raw = "|".join(str(part or "") for part in parts).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def google_news_url(query: str) -> str:
    timed_query = f"{query} when:{LOOKBACK_DAYS}d"
    encoded = quote_plus(timed_query)
    return (
        f"https://news.google.com/rss/search?q={encoded}"
        "&hl=en-US&gl=US&ceid=US:en"
    )


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_google_news(section: str, query: str) -> tuple[list[dict], str | None]:
    try:
        response = requests.get(
            google_news_url(query), headers=HEADERS, timeout=25
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return [], f"{section}: {exc}"

    feed = feedparser.parse(response.content)
    items = []

    for entry in feed.entries:
        title = clean_spaces(entry.get("title", "Untitled"))
        source_obj = entry.get("source", {})
        if isinstance(source_obj, dict):
            source = clean_spaces(source_obj.get("title", ""))
        else:
            source = clean_spaces(getattr(source_obj, "title", ""))

        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)].strip()
        elif " - " in title:
            possible_title, possible_source = title.rsplit(" - ", 1)
            if 1 < len(possible_source) < 80:
                title = possible_title.strip()
                source = source or possible_source.strip()

        published = parse_rss_date(entry.get("published", ""))
        summary = strip_html(entry.get("summary", ""))
        if (
            len(summary) < 45
            or normalize_title(title) in normalize_title(summary)
            or "http" in summary
        ):
            summary = ""

        url = entry.get("link", "")
        items.append(
            {
                "id": stable_id(section, url, title),
                "article_id": stable_id(url, normalize_title(title)),
                "section": section,
                "title": title,
                "summary": summary,
                "source": source or "Google News",
                "url": url,
                "published": published.isoformat(),
                "date_label": published.strftime("%b. %d, %Y").replace(" 0", " "),
                "tag": section,
                "origin": "Google News RSS",
            }
        )

    return items, None


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_federal_register() -> tuple[list[dict], list[str]]:
    cutoff = (datetime.now(EASTERN) - timedelta(days=LOOKBACK_DAYS)).date().isoformat()
    endpoint = "https://www.federalregister.gov/api/v1/documents.json"
    items = []
    errors = []

    for term in FEDERAL_REGISTER_TERMS:
        params = {
            "per_page": 20,
            "order": "newest",
            "conditions[term]": term,
            "conditions[publication_date][gte]": cutoff,
        }
        try:
            response = requests.get(
                endpoint, params=params, headers=HEADERS, timeout=25
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            errors.append(f'Federal Register search "{term}": {exc}')
            continue

        for result in data.get("results", []):
            title = clean_spaces(result.get("title", "Untitled federal action"))
            agencies = ", ".join(
                agency.get("name", "")
                for agency in result.get("agencies", [])
                if agency.get("name")
            )
            publication_date = result.get("publication_date", cutoff)
            try:
                published = datetime.fromisoformat(publication_date).replace(
                    tzinfo=EASTERN
                )
            except ValueError:
                published = datetime.now(EASTERN)

            abstract = strip_html(result.get("abstract", ""))
            if len(abstract) > 700:
                abstract = abstract[:697].rsplit(" ", 1)[0] + "…"

            url = result.get("html_url") or result.get("pdf_url") or ""
            items.append(
                {
                    "id": stable_id("Federal Actions", url, title),
                    "article_id": stable_id(url, normalize_title(title)),
                    "section": "Federal Actions",
                    "title": title,
                    "summary": abstract,
                    "source": agencies or "Federal Register",
                    "url": url,
                    "published": published.isoformat(),
                    "date_label": published.strftime("%b. %d, %Y").replace(" 0", " "),
                    "tag": result.get("type", "Federal action"),
                    "origin": "Federal Register API",
                }
            )

    return items, errors


def deduplicate(items: list[dict]) -> list[dict]:
    seen_urls = set()
    seen_titles = set()
    unique = []
    for item in sorted(items, key=lambda x: x.get("published", ""), reverse=True):
        normalized = normalize_title(item["title"])
        url = item.get("url", "")
        if url and url in seen_urls:
            continue
        if normalized and normalized in seen_titles:
            continue
        if url:
            seen_urls.add(url)
        if normalized:
            seen_titles.add(normalized)
        unique.append(item)
    return unique


@st.cache_data(ttl=3600, show_spinner="Loading current public-source news…")
def load_all_news() -> tuple[dict[str, list[dict]], list[str]]:
    briefing: dict[str, list[dict]] = {}
    errors = []

    for section, query in NEWS_QUERIES.items():
        items, error = fetch_google_news(section, query)
        briefing[section] = deduplicate(items)[:ITEMS_PER_SECTION]
        if error:
            errors.append(error)

    federal_items, federal_errors = fetch_federal_register()
    briefing["Federal Actions"] = deduplicate(federal_items)[:ITEMS_PER_SECTION]
    errors.extend(federal_errors)

    # Raw Top Developments is only a fallback before an AI analysis is run.
    candidates = []
    for section in TOPIC_SECTIONS:
        for item in briefing.get(section, [])[:2]:
            copied = dict(item)
            copied["section"] = "Top Developments"
            copied["tag"] = section
            copied["id"] = stable_id("Top Developments", copied["article_id"])
            candidates.append(copied)
    briefing["Top Developments"] = deduplicate(candidates)[:ITEMS_PER_SECTION]

    for section in SECTION_ORDER:
        briefing.setdefault(section, [])
    return briefing, errors


def flatten_for_ai(briefing: dict[str, list[dict]]) -> list[dict]:
    registry: dict[str, dict] = {}
    for section, items in briefing.items():
        if section == "Top Developments":
            continue
        for item in items:
            key = item["article_id"]
            if key not in registry:
                registry[key] = {
                    **item,
                    "candidate_sections": [section],
                }
            elif section not in registry[key]["candidate_sections"]:
                registry[key]["candidate_sections"].append(section)
    return sorted(
        registry.values(), key=lambda x: x.get("published", ""), reverse=True
    )


def secret_value(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, default)
    except Exception:
        return default
    return str(value or default)


def owner_authenticated() -> bool:
    configured = secret_value("owner_password")
    if not configured:
        return False
    return bool(st.session_state.get("owner_authenticated", False))


def render_owner_access() -> None:
    st.sidebar.header("Owner controls")
    configured = secret_value("owner_password")
    if not configured:
        st.sidebar.warning(
            "Add owner_password in Streamlit Secrets before enabling AI controls."
        )
        return

    if owner_authenticated():
        st.sidebar.success("Owner access unlocked")
        if st.sidebar.button("Lock owner controls"):
            st.session_state["owner_authenticated"] = False
            st.rerun()
        return

    attempted = st.sidebar.text_input("Owner password", type="password")
    if st.sidebar.button("Unlock"):
        if hmac.compare_digest(attempted, configured):
            st.session_state["owner_authenticated"] = True
            st.rerun()
        else:
            st.sidebar.error("Incorrect password")




def openai_model_candidates() -> list[str]:
    """Return the preferred OpenAI model followed by distinct fallbacks."""
    preferred = secret_value("openai_model", DEFAULT_OPENAI_MODEL).strip()

    configured_fallbacks = secret_value("openai_fallback_models", "")
    extra = [
        value.strip()
        for value in configured_fallbacks.split(",")
        if value.strip()
    ]

    candidates = [preferred, *extra, *DEFAULT_OPENAI_FALLBACK_MODELS]
    unique = []
    seen = set()
    for model in candidates:
        if model and model not in seen:
            seen.add(model)
            unique.append(model)
    return unique


def news_analysis_schema() -> dict:
    """JSON Schema used by OpenAI Structured Outputs."""
    cluster_schema = {
        "type": "object",
        "properties": {
            "cluster_id": {"type": "string"},
            "article_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
            "primary_article_id": {"type": "string"},
            "section": {
                "type": "string",
                "enum": TOPIC_SECTIONS,
            },
            "relevant": {"type": "boolean"},
            "importance": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
            },
            "canonical_title": {"type": "string"},
            "summary": {"type": "string"},
            "is_administration_win": {"type": "boolean"},
            "eo_number": {"type": "string"},
            "eo_section": {"type": "string"},
            "win_explanation": {"type": "string"},
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
            "exclude_reason": {"type": "string"},
        },
        "required": [
            "cluster_id",
            "article_ids",
            "primary_article_id",
            "section",
            "relevant",
            "importance",
            "canonical_title",
            "summary",
            "is_administration_win",
            "eo_number",
            "eo_section",
            "win_explanation",
            "confidence",
            "exclude_reason",
        ],
        "additionalProperties": False,
    }

    return {
        "type": "object",
        "properties": {
            "clusters": {
                "type": "array",
                "items": cluster_schema,
            }
        },
        "required": ["clusters"],
        "additionalProperties": False,
    }


def extract_openai_output_text(data: dict) -> str:
    """Extract assistant text from a raw Responses API response."""
    text_parts = []
    refusals = []

    for output_item in data.get("output", []):
        if output_item.get("type") != "message":
            continue
        for content_item in output_item.get("content", []):
            item_type = content_item.get("type")
            if item_type == "output_text":
                text_parts.append(content_item.get("text", ""))
            elif item_type == "refusal":
                refusals.append(content_item.get("refusal", "Request refused."))

    if refusals:
        raise RuntimeError("OpenAI declined the request: " + " ".join(refusals))

    text = "".join(text_parts).strip()
    if not text:
        status = data.get("status", "unknown")
        incomplete = data.get("incomplete_details") or {}
        reason = incomplete.get("reason", "")
        detail = f" Status: {status}."
        if reason:
            detail += f" Incomplete reason: {reason}."
        raise RuntimeError("OpenAI returned no usable text." + detail)

    return text


def estimate_openai_cost(model: str, usage: dict) -> float | None:
    prices = OPENAI_TOKEN_PRICES.get(model)
    if not prices:
        return None

    input_tokens = int(usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)

    return (
        input_tokens * prices["input"] / 1_000_000
        + output_tokens * prices["output"] / 1_000_000
    )


def openai_chat(messages: list[dict]) -> str:
    """
    Call the OpenAI Responses API with Structured Outputs.

    Transient errors are retried with exponential backoff. If the
    preferred model remains unavailable, the app tries the configured
    fallback model.
    """
    api_key = secret_value("openai_api_key")
    if not api_key:
        raise RuntimeError(
            "The openai_api_key secret is missing from Streamlit settings."
        )

    input_messages = [
        {
            "role": message.get("role", "user"),
            "content": message.get("content", ""),
        }
        for message in messages
    ]

    attempt_errors = []

    for model in openai_model_candidates():
        payload = {
            "model": model,
            "input": input_messages,
            "reasoning": {"effort": "none"},
            "max_output_tokens": 16000,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "transportation_news_analysis",
                    "strict": True,
                    "schema": news_analysis_schema(),
                }
            },
        }

        for attempt in range(3):
            try:
                response = requests.post(
                    OPENAI_RESPONSES_ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=180,
                )
            except requests.RequestException as exc:
                attempt_errors.append(f"{model}: network error: {exc}")
                if attempt < 2:
                    delay = (2 ** attempt) + random.uniform(0.25, 0.9)
                    time.sleep(delay)
                    continue
                break

            if response.status_code < 400:
                try:
                    data = response.json()
                    text = extract_openai_output_text(data)
                    usage = data.get("usage") or {}
                    estimated_cost = estimate_openai_cost(model, usage)

                    st.session_state["last_openai_model_used"] = model
                    st.session_state["last_openai_usage"] = usage
                    st.session_state["last_openai_estimated_cost"] = estimated_cost
                    return text
                except (ValueError, KeyError, TypeError) as exc:
                    attempt_errors.append(
                        f"{model}: unexpected successful response: {exc}"
                    )
                    break

            detail = clean_spaces(response.text)[:700]
            attempt_errors.append(
                f"{model}: HTTP {response.status_code}: {detail}"
            )

            if response.status_code in TRANSIENT_OPENAI_STATUS_CODES:
                if attempt < 2:
                    delay = (2 ** attempt) + random.uniform(0.25, 0.9)
                    time.sleep(delay)
                    continue
                break

            # A missing/unavailable model may be resolved by the fallback.
            if response.status_code == 404:
                break

            if response.status_code == 401:
                raise RuntimeError(
                    "OpenAI rejected the API key. Recopy the project API key "
                    "into Streamlit Secrets and make sure billing is active."
                )

            if response.status_code == 429:
                raise RuntimeError(
                    "OpenAI rate or billing limit reached. Check the API Usage "
                    "and Limits pages in the OpenAI Platform."
                )

            raise RuntimeError(
                f"OpenAI API returned HTTP {response.status_code}: {detail}"
            )

    compact_errors = " | ".join(attempt_errors[-6:])
    raise RuntimeError(
        "OpenAI was unable to complete the analysis after automatic retries "
        "and the fallback model. Try Run AI Analysis again. "
        f"Recent attempts: {compact_errors}"
    )

def extract_json_object(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("The model did not return a JSON object.")
    return json.loads(cleaned[start : end + 1])


def ai_prompt_payload(articles: list[dict]) -> list[dict]:
    compact_articles = []
    for item in articles:
        compact_articles.append(
            {
                "article_id": item["article_id"],
                "title": item["title"],
                "source": item["source"],
                "date": item["date_label"],
                "candidate_sections": item["candidate_sections"],
                "available_snippet": item.get("summary", ""),
            }
        )

    system = f"""
You are the senior editor of a concise U.S. advanced-transportation policy news update.
You receive public-source article metadata, often only headlines and short snippets.
Do not invent facts beyond the supplied metadata. You may make cautious, clearly grounded
inferences from multiple aligned headlines. Your tasks are to remove irrelevant results,
group coverage of the same underlying event, choose the best primary source, classify the
story, rank importance, and draft concise editorial copy.

{SOURCE_PREFERENCE}

{EO_REFERENCE}

ADMINISTRATION-WIN STANDARD
Mark is_administration_win true only when the reported event directly fulfills, implements,
advances, or provides a concrete result under a listed Trump executive-order mandate, or is
an independently verifiable Trump 47 regulatory/operational achievement evident from the
supplied metadata. Merely positive private-sector news is not automatically an Administration
win. For each true win, write exactly one sentence of 30-55 words explaining the concrete
American policy or operational result. Use confident, patriotic, pro-American language
emphasizing American leadership, security, innovation, workers, manufacturing, or
competitiveness as appropriate. The app separately displays the full EO title, number, and
section, so do not begin with "This is a win for EO..." and do not repeat the citation merely
to fill space. Stay factual and do not overclaim causation. If support is uncertain, set false.
Use exact eo_number values "EO 14307", "EO 14305", or "EO 14304" and a concise eo_section
such as "Sec. 6".

RELEVANCE STANDARD
Exclude keyword collisions, consumer products, generic AI features, celebrity commentary,
stock-promotion or valuation stories, routine foreign developments without a material U.S.
policy/competitive implication, and stories outside the listed portfolio. If the best honest
summary would say that the stories are "broader mobility-tech stories," "routine notices,"
"not materially relevant," or merely share a market theme, mark them irrelevant. Never keep
weak material simply to populate every section.

CLUSTERING STANDARD
Cluster articles only when they report the same concrete event, announcement, rule,
deployment, flight, contract, facility, or government action. Sharing a broad topic is NOT
enough. Distinct companies, cities, deployments, regulatory actions, or research findings
must be separate clusters even when they illustrate the same trend. For example, an Amazon
Cleveland launch, a Manna Tulsa expansion, and a Zipline Tampa network are three stories,
not one "drone delivery expands" cluster.

Most clusters should contain one to four articles. A cluster with more than five articles
should be rare and must clearly concern the same named event. Avoid umbrella clusters and
generic canonical headlines such as "market activity grows," "stories span...," or
"developments surface across markets." Select one primary_article_id and put only true
same-event coverage in article_ids so the app can display "Also covered by." Do not create
multiple clusters for the same event.

OUTPUT
Return only valid JSON, no Markdown, matching this shape:
{{
  "clusters": [
    {{
      "cluster_id": "short_unique_id",
      "article_ids": ["id1", "id2"],
      "primary_article_id": "id1",
      "section": "one exact section from the allowed list",
      "relevant": true,
      "importance": 1,
      "canonical_title": "concise factual headline",
      "summary": "one or two specific, cautious sentences, no more than 60 words total",
      "is_administration_win": false,
      "eo_number": "",
      "eo_section": "",
      "win_explanation": "",
      "confidence": "high",
      "exclude_reason": ""
    }}
  ]
}}
Allowed section values: {json.dumps(TOPIC_SECTIONS)}.
Importance is an integer 1-10. Confidence is high, medium, or low.
Include every supplied article ID exactly once across the clusters. Irrelevant articles may
be single-item clusters with relevant=false and a concise exclude_reason.
""".strip()

    user = (
        "Analyze and organize these candidate articles:\n"
        + json.dumps(compact_articles, ensure_ascii=False)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def validate_ai_result(result: dict, articles: list[dict]) -> dict:
    valid_ids = {item["article_id"] for item in articles}
    seen = set()
    cleaned_clusters = []

    clusters = result.get("clusters", [])
    if not isinstance(clusters, list):
        raise ValueError("AI result did not contain a clusters list.")

    for index, cluster in enumerate(clusters):
        if not isinstance(cluster, dict):
            continue
        article_ids = [
            article_id
            for article_id in cluster.get("article_ids", [])
            if article_id in valid_ids and article_id not in seen
        ]
        primary = cluster.get("primary_article_id")
        if primary in valid_ids and primary not in article_ids and primary not in seen:
            article_ids.insert(0, primary)
        if not article_ids:
            continue

        seen.update(article_ids)
        if primary not in article_ids:
            primary = article_ids[0]

        section = cluster.get("section")
        if section not in TOPIC_SECTIONS:
            section = "Other Advanced Transportation"

        try:
            importance = int(cluster.get("importance", 5))
        except (TypeError, ValueError):
            importance = 5
        importance = max(1, min(10, importance))

        cleaned_clusters.append(
            {
                "cluster_id": clean_spaces(cluster.get("cluster_id", ""))
                or f"cluster_{index + 1}",
                "article_ids": article_ids,
                "primary_article_id": primary,
                "section": section,
                "relevant": bool(cluster.get("relevant", False)),
                "importance": importance,
                "canonical_title": clean_spaces(cluster.get("canonical_title", "")),
                "summary": clean_spaces(cluster.get("summary", "")),
                "is_administration_win": bool(
                    cluster.get("is_administration_win", False)
                ),
                "eo_number": clean_spaces(cluster.get("eo_number", "")),
                "eo_section": clean_spaces(cluster.get("eo_section", "")),
                "win_explanation": clean_spaces(
                    cluster.get("win_explanation", "")
                ),
                "confidence": clean_spaces(cluster.get("confidence", "medium")).lower(),
                "exclude_reason": clean_spaces(cluster.get("exclude_reason", "")),
            }
        )

    # Preserve any omitted IDs as excluded records so nothing silently disappears.
    for article_id in sorted(valid_ids - seen):
        cleaned_clusters.append(
            {
                "cluster_id": f"unclassified_{article_id}",
                "article_ids": [article_id],
                "primary_article_id": article_id,
                "section": "Other Advanced Transportation",
                "relevant": False,
                "importance": 1,
                "canonical_title": "",
                "summary": "",
                "is_administration_win": False,
                "eo_number": "",
                "eo_section": "",
                "win_explanation": "",
                "confidence": "low",
                "exclude_reason": "The AI response omitted this item; review manually.",
            }
        )

    return {"clusters": cleaned_clusters}


def run_ai_analysis(articles: list[dict]) -> dict:
    raw = openai_chat(ai_prompt_payload(articles))
    parsed = extract_json_object(raw)
    return validate_ai_result(parsed, articles)


def article_lookup(articles: list[dict]) -> dict[str, dict]:
    return {item["article_id"]: item for item in articles}


def cluster_to_item(cluster: dict, lookup: dict[str, dict]) -> dict:
    primary = lookup[cluster["primary_article_id"]]
    related = [
        lookup[article_id]
        for article_id in cluster["article_ids"]
        if article_id != cluster["primary_article_id"] and article_id in lookup
    ]
    return {
        "id": stable_id("ai", cluster["cluster_id"], *cluster["article_ids"]),
        "cluster_id": cluster["cluster_id"],
        "title": cluster["canonical_title"] or primary["title"],
        "summary": cluster["summary"],
        "source": primary["source"],
        "url": primary["url"],
        "published": primary["published"],
        "date_label": primary["date_label"],
        "tag": cluster["section"],
        "section": cluster["section"],
        "importance": cluster["importance"],
        "is_administration_win": cluster["is_administration_win"],
        "eo_number": cluster["eo_number"],
        "eo_section": cluster["eo_section"],
        "win_explanation": cluster["win_explanation"],
        "confidence": cluster["confidence"],
        "also_covered": [
            {
                "source": item["source"],
                "url": item["url"],
                "title": item["title"],
            }
            for item in related
        ],
        "article_ids": cluster["article_ids"],
    }


def initialize_ai_editor(items: list[dict]) -> None:
    for item in items:
        prefix = f'ai_{item["id"]}'
        defaults = {
            f"{prefix}_include": True,
            f"{prefix}_title": item["title"],
            f"{prefix}_summary": item["summary"],
            f"{prefix}_is_win": item["is_administration_win"],
            f"{prefix}_eo_number": item["eo_number"],
            f"{prefix}_eo_section": item["eo_section"],
            f"{prefix}_win": item["win_explanation"],
            f"{prefix}_section": item["section"],
            f"{prefix}_importance": item["importance"],
        }
        for key, value in defaults.items():
            if key not in st.session_state:
                st.session_state[key] = value


def edited_ai_items(items: list[dict]) -> list[dict]:
    edited = []
    for item in items:
        prefix = f'ai_{item["id"]}'
        if not st.session_state.get(f"{prefix}_include", True):
            continue
        copied = dict(item)
        copied["title"] = st.session_state.get(f"{prefix}_title", item["title"]).strip()
        copied["summary"] = st.session_state.get(
            f"{prefix}_summary", item["summary"]
        ).strip()
        copied["is_administration_win"] = st.session_state.get(
            f"{prefix}_is_win", item["is_administration_win"]
        )
        copied["eo_number"] = st.session_state.get(
            f"{prefix}_eo_number", item["eo_number"]
        ).strip()
        copied["eo_section"] = st.session_state.get(
            f"{prefix}_eo_section", item["eo_section"]
        ).strip()
        copied["win_explanation"] = st.session_state.get(
            f"{prefix}_win", item["win_explanation"]
        ).strip()
        copied["section"] = st.session_state.get(
            f"{prefix}_section", item["section"]
        )
        copied["tag"] = copied["section"]
        copied["importance"] = int(
            st.session_state.get(f"{prefix}_importance", item["importance"])
        )
        edited.append(copied)
    return edited


def build_ai_briefing(items: list[dict]) -> dict[str, list[dict]]:
    briefing = {section: [] for section in SECTION_ORDER}
    wins = sorted(
        [item for item in items if item["is_administration_win"]],
        key=lambda x: (x["importance"], x.get("published", "")),
        reverse=True,
    )
    briefing["Trump Administration Wins"] = wins

    remaining = [item for item in items if not item["is_administration_win"]]
    top = sorted(
        [item for item in remaining if item["importance"] >= 7],
        key=lambda x: (x["importance"], x.get("published", "")),
        reverse=True,
    )[:5]
    top_ids = {item["id"] for item in top}
    briefing["Top Developments"] = top

    for item in remaining:
        if item["id"] in top_ids:
            continue
        section = item["section"]
        briefing.setdefault(section, []).append(item)

    for section in TOPIC_SECTIONS:
        briefing[section] = sorted(
            briefing.get(section, []),
            key=lambda x: (x["importance"], x.get("published", "")),
            reverse=True,
        )
    return briefing


def initialize_raw_editor(briefing: dict[str, list[dict]]) -> None:
    for section in SECTION_ORDER:
        for item in briefing.get(section, []):
            include_key = f'raw_include_{item["id"]}'
            summary_key = f'raw_summary_{item["id"]}'
            if include_key not in st.session_state:
                st.session_state[include_key] = section != "Trump Administration Wins"
            if summary_key not in st.session_state:
                st.session_state[summary_key] = item.get("summary", "")


def selected_raw_briefing(briefing: dict[str, list[dict]]) -> dict[str, list[dict]]:
    selected = {section: [] for section in SECTION_ORDER}
    for section in SECTION_ORDER:
        for item in briefing.get(section, []):
            if st.session_state.get(f'raw_include_{item["id"]}', False):
                copied = dict(item)
                copied["summary"] = st.session_state.get(
                    f'raw_summary_{item["id"]}', ""
                ).strip()
                copied.setdefault("also_covered", [])
                copied.setdefault("win_explanation", "")
                copied.setdefault("eo_number", "")
                copied.setdefault("eo_section", "")
                selected[section].append(copied)
    return selected



def executive_order_display(eo_number: str, eo_section: str) -> str:
    """Return a reader-friendly EO title, number, and section."""
    number = clean_spaces(eo_number)
    section = clean_spaces(eo_section)
    name = EO_DISPLAY_NAMES.get(number, "")

    citation_bits = [bit for bit in [number, section] if bit]
    citation = ", ".join(citation_bits)

    if name and citation:
        return f"{name} ({citation})"
    if name:
        return name
    return citation


def deduplicated_related_sources(related: list[dict]) -> list[dict]:
    """Keep one link per outlet and omit the primary outlet elsewhere."""
    unique = []
    seen = set()
    for item in related:
        source = clean_spaces(item.get("source", "Related coverage"))
        key = source.casefold()
        if not source or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique

def safe_url(value: str) -> str:
    value = (value or "").strip()
    if value.startswith(("https://", "http://")):
        return html.escape(value, quote=True)
    return "#"



def article_html(item: dict, display_section: str = "") -> str:
    title = html.escape(item["title"])
    summary = html.escape(item.get("summary", ""))
    source = html.escape(item.get("source", "Source"))
    original_section = html.escape(item.get("tag", ""))
    date_label = html.escape(item.get("date_label", ""))
    url = safe_url(item.get("url", ""))

    is_win = bool(item.get("is_administration_win"))
    accent = "#b42318" if is_win else "#d6e0e8"
    card_background = "#fffaf7" if is_win else "#ffffff"

    category_markup = ""
    if display_section == "Top Developments" and original_section:
        category_markup = f"""
        <div style="display:inline-block;margin:0 0 6px 0;padding:3px 7px;
            border-radius:10px;background:#eef3f7;color:#365a78;
            font-size:10px;line-height:1.2;font-weight:700;letter-spacing:.3px;
            text-transform:uppercase;">
          {original_section}
        </div>
        """

    summary_markup = ""
    if summary:
        summary_markup = f"""
        <div style="font-size:15px;line-height:1.55;color:#222;margin-bottom:8px;">
          {summary}
        </div>
        """

    win_markup = ""
    if item.get("win_explanation"):
        eo_display = executive_order_display(
            item.get("eo_number", ""),
            item.get("eo_section", ""),
        )
        eo_markup = (
            f'<div style="font-size:12px;line-height:1.4;color:#8b2c26;'
            f'margin-bottom:4px;font-weight:700;">'
            f'{html.escape(eo_display)}</div>'
            if eo_display
            else ""
        )
        win_markup = f"""
        <div style="background:#fff1ed;border-left:3px solid #b42318;
            padding:10px 12px;margin:9px 0 10px 0;">
          <div style="font-size:13px;line-height:1.3;color:#7a271a;
              font-weight:800;margin-bottom:3px;">
            WHY THIS IS A TRUMP ADMINISTRATION WIN
          </div>
          {eo_markup}
          <div style="font-size:14px;line-height:1.5;color:#5f201a;">
            {html.escape(item['win_explanation'])}
          </div>
        </div>
        """

    related = deduplicated_related_sources(item.get("also_covered", []))
    related_markup = ""
    if related:
        visible = related[:5]
        links = []
        for related_item in visible:
            links.append(
                f'<a href="{safe_url(related_item.get("url", ""))}" '
                f'style="color:#60758b;text-decoration:underline;">'
                f'{html.escape(related_item.get("source", "Related coverage"))}</a>'
            )
        more = len(related) - len(visible)
        suffix = f" · +{more} more" if more > 0 else ""
        related_markup = f"""
        <div style="font-size:11px;line-height:1.5;color:#77818b;margin-top:7px;">
          <strong>Also covered by:</strong> {' · '.join(links)}{suffix}
        </div>
        """

    return f"""
    <div style="margin:0 0 17px 0;padding:12px 14px 12px 14px;
        border-left:3px solid {accent};background:{card_background};">
      {category_markup}
      <div style="font-size:17px;line-height:1.35;font-weight:700;margin-bottom:5px;">
        <a href="{url}" style="color:#153a66;text-decoration:none;">{title}</a>
      </div>
      {summary_markup}
      {win_markup}
      <div style="font-size:12px;line-height:1.4;color:#666;">
        {source}
        {f' &nbsp;•&nbsp; {date_label}' if date_label else ''}
        &nbsp;•&nbsp; <a href="{url}" style="color:#46698e;">Read source</a>
      </div>
      {related_markup}
    </div>
    """


def build_email_html(briefing: dict[str, list[dict]], display_date: str) -> str:
    sections = []
    total_items = sum(len(items) for items in briefing.values())
    win_count = len(briefing.get("Trump Administration Wins", []))
    top_count = len(briefing.get("Top Developments", []))

    for section_name in SECTION_ORDER:
        items = briefing.get(section_name, [])
        if not items:
            continue

        is_wins = section_name == "Trump Administration Wins"
        is_top = section_name == "Top Developments"
        heading_color = "#8a1c1c" if is_wins else "#173a5e"
        border_color = "#b42318" if is_wins else "#c8d5df"
        background = "#fff8f4" if is_wins else "#ffffff"
        subheading = (
            "Presidential priorities, executive-order implementation, and concrete American results"
            if is_wins
            else (
                "The most consequential developments across the portfolio"
                if is_top
                else ""
            )
        )

        stories = "".join(article_html(item, section_name) for item in items)
        subheading_markup = (
            f'<div style="font-size:12px;line-height:1.45;color:#687786;'
            f'margin:-7px 0 14px 0;">{html.escape(subheading)}</div>'
            if subheading
            else ""
        )

        sections.append(
            f"""
            <div style="margin:0 0 27px 0;padding:{'18px' if is_wins else '0'};
                border:{'1px solid ' + border_color if is_wins else '0'};
                border-radius:{'8px' if is_wins else '0'};background:{background};">
              <div style="color:{heading_color};font-size:20px;line-height:1.3;
                  font-weight:800;border-bottom:2px solid {border_color};
                  padding-bottom:7px;margin-bottom:14px;">
                {html.escape(section_name)}
              </div>
              {subheading_markup}
              {stories}
            </div>
            """
        )

    glance_parts = []
    if win_count:
        glance_parts.append(f"{win_count} Administration win{'s' if win_count != 1 else ''}")
    if top_count:
        glance_parts.append(f"{top_count} top development{'s' if top_count != 1 else ''}")
    glance_parts.append(f"{total_items} total item{'s' if total_items != 1 else ''}")
    glance = " &nbsp;•&nbsp; ".join(glance_parts)

    return f"""
    <div style="max-width:760px;margin:0 auto;padding:0 4px 30px 4px;
        font-family:Arial,Helvetica,sans-serif;color:#1f2933;background:#ffffff;">
      <div style="background:#123552;padding:20px 22px 18px 22px;margin-bottom:18px;">
        <div style="font-size:30px;line-height:1.15;font-weight:800;color:#ffffff;">
          News Update
        </div>
        <div style="font-size:14px;line-height:1.5;color:#dbe7ef;margin-top:5px;">
          {html.escape(display_date)}
        </div>
        <div style="font-size:13px;line-height:1.5;color:#dbe7ef;margin-top:1px;">
          UAS, Advanced Transportation, and Airspace Policy
        </div>
      </div>
      <div style="font-size:11px;line-height:1.4;color:#5d6b78;
          background:#f2f5f7;padding:8px 11px;margin-bottom:22px;">
        <strong>AT A GLANCE:</strong> {glance}
      </div>
      {''.join(sections)}
      <div style="margin-top:30px;padding-top:12px;border-top:1px solid #d7dde4;
          color:#777;font-size:11px;line-height:1.5;">
        Public-source, AI-assisted news update. Review all summaries, links,
        executive-order citations, and Administration attributions before distribution.
      </div>
    </div>
    """

def build_plain_text(briefing: dict[str, list[dict]], display_date: str) -> str:
    lines = [
        "NEWS UPDATE",
        display_date,
        "UAS, Advanced Transportation, and Airspace Policy",
        "",
    ]
    for section_name in SECTION_ORDER:
        items = briefing.get(section_name, [])
        if not items:
            continue
        lines.extend([section_name.upper(), ""])
        for item in items:
            lines.append(item["title"])
            if item.get("summary"):
                lines.append(item["summary"])
            if item.get("win_explanation"):
                eo_display = executive_order_display(
                    item.get("eo_number", ""),
                    item.get("eo_section", ""),
                )
                lines.append("WHY THIS IS A TRUMP ADMINISTRATION WIN")
                if eo_display:
                    lines.append(eo_display)
                lines.append(item["win_explanation"])
            lines.append(
                " | ".join(
                    part for part in [item.get("source", ""), item.get("date_label", ""), item.get("url", "")] if part
                )
            )
            related = deduplicated_related_sources(item.get("also_covered", []))
            if related:
                visible = related[:5]
                coverage = " | ".join(
                    f"{related_item.get('source', 'Related')}: {related_item.get('url', '')}"
                    for related_item in visible
                )
                if len(related) > len(visible):
                    coverage += f" | +{len(related) - len(visible)} more"
                lines.append("Also covered by: " + coverage)
            lines.append("")
    lines.append(
        "Public-source, AI-assisted update. Review all summaries and attributions before distribution."
    )
    return "\n".join(lines)


def copy_buttons(email_html: str, plain_text: str) -> None:
    html_js = json.dumps(email_html)
    plain_js = json.dumps(plain_text)
    component = f"""
    <!doctype html><html><head><meta charset="utf-8"><style>
      body {{margin:0;font-family:Arial,Helvetica,sans-serif;background:transparent;}}
      .row {{display:flex;gap:10px;flex-wrap:wrap;align-items:center;}}
      button {{border:1px solid #153a66;border-radius:7px;padding:10px 15px;
        font-size:14px;font-weight:700;cursor:pointer;}}
      #rich {{background:#153a66;color:white;}} #plain {{background:white;color:#153a66;}}
      #status {{color:#46606f;font-size:13px;min-height:18px;}}
    </style></head><body><div class="row">
      <button id="rich" onclick="copyRich()">Copy for Email</button>
      <button id="plain" onclick="copyPlain()">Copy Plain Text</button>
      <span id="status"></span></div><script>
      const richContent={html_js}; const plainContent={plain_js};
      function showStatus(message){{const s=document.getElementById('status');s.textContent=message;
        setTimeout(()=>s.textContent='',2500);}}
      async function copyRich(){{try{{const item=new ClipboardItem({{
        'text/html':new Blob([richContent],{{type:'text/html'}}),
        'text/plain':new Blob([plainContent],{{type:'text/plain'}})}});
        await navigator.clipboard.write([item]);showStatus('Copied with formatting.');}}
        catch(error){{try{{await navigator.clipboard.writeText(plainContent);
        showStatus('Formatting was blocked; copied plain text.');}}
        catch(e){{showStatus('Clipboard blocked by the browser.');}}}}}}
      async function copyPlain(){{try{{await navigator.clipboard.writeText(plainContent);
        showStatus('Plain text copied.');}}catch(e){{showStatus('Clipboard blocked by the browser.');}}}}
    </script></body></html>
    """
    components.html(component, height=60)


def render_ai_review(items: list[dict]) -> None:
    st.markdown(
        "AI has removed irrelevant hits, combined duplicate coverage, selected a primary "
        "source, and drafted summaries. Every field remains editable."
    )
    for item in sorted(items, key=lambda x: (x["is_administration_win"], x["importance"]), reverse=True):
        prefix = f'ai_{item["id"]}'
        with st.container(border=True):
            header_left, header_right = st.columns([0.76, 0.24])
            with header_left:
                st.markdown(f'**[{item["title"]}]({item["url"]})**')
                st.caption(
                    f'{item["source"]} · {item["date_label"]} · '
                    f'AI confidence: {item["confidence"]} · Importance: {item["importance"]}/10'
                )
            with header_right:
                st.checkbox("Include", key=f"{prefix}_include")
                st.checkbox("Administration win", key=f"{prefix}_is_win")

            st.text_input("Headline", key=f"{prefix}_title")
            st.text_area("Summary", key=f"{prefix}_summary", height=95)

            c1, c2, c3 = st.columns([0.48, 0.32, 0.20])
            with c1:
                st.selectbox("Section", TOPIC_SECTIONS, key=f"{prefix}_section")
            with c2:
                st.text_input("Executive order", key=f"{prefix}_eo_number")
            with c3:
                st.text_input("Section", key=f"{prefix}_eo_section")
            st.text_area(
                "Why this is a win",
                key=f"{prefix}_win",
                height=90,
                help="Used only when Administration win is checked.",
            )
            st.slider("Importance", 1, 10, key=f"{prefix}_importance")

            if item.get("also_covered"):
                st.caption(
                    "Also covered by: "
                    + " · ".join(
                        f'[{related["source"]}]({related["url"]})'
                        for related in item["also_covered"]
                    )
                )


def render_raw_review(briefing: dict[str, list[dict]]) -> None:
    st.warning(
        "This is the unfiltered feed. Run AI Analysis for relevance filtering, clustering, "
        "draft summaries, and executive-order mapping."
    )
    for section in SECTION_ORDER:
        st.subheader(section)
        items = briefing.get(section, [])
        if not items:
            st.caption("No matching items found.")
            continue
        for item in items:
            with st.container(border=True):
                left, right = st.columns([0.78, 0.22])
                with left:
                    st.markdown(f'**[{item["title"]}]({item["url"]})**')
                    st.caption(f'{item["source"]} · {item["date_label"]} · {item["origin"]}')
                with right:
                    st.checkbox("Include", key=f'raw_include_{item["id"]}')
                st.text_area(
                    "Summary",
                    key=f'raw_summary_{item["id"]}',
                    height=80,
                    label_visibility="collapsed",
                )
        st.divider()


# PAGE
st.markdown(
    """
    <style>
      .block-container {max-width:1180px;padding-top:1.5rem;padding-bottom:4rem;}
      [data-testid="stHeader"] {background:rgba(255,255,255,0.92);}
    </style>
    """,
    unsafe_allow_html=True,
)

render_owner_access()
today = datetime.now(EASTERN)
display_date = today.strftime("%A, %B %d, %Y").replace(" 0", " ")

st.title("News Update")
st.caption(
    f"Public-source, AI-assisted prototype · preceding {LOOKBACK_DAYS} days · "
    f"page run {today.strftime('%-I:%M %p')} Eastern"
)

button_col, note_col = st.columns([0.22, 0.78])
with button_col:
    if st.button("Refresh live feeds", use_container_width=True):
        st.cache_data.clear()
        for key in list(st.session_state.keys()):
            if key != "owner_authenticated":
                del st.session_state[key]
        st.rerun()
with note_col:
    st.caption("Feed results are cached for one hour. AI analysis runs only when you request it.")

raw_briefing, source_errors = load_all_news()
initialize_raw_editor(raw_briefing)
articles = flatten_for_ai(raw_briefing)
lookup = article_lookup(articles)

ai_tab, preview_tab, raw_tab, status_tab = st.tabs(
    ["1. AI Review", "2. Email Preview", "Raw Feed", "Source Status"]
)

with ai_tab:
    token_configured = bool(secret_value("openai_api_key"))
    if not token_configured:
        st.warning(
            "OpenAI is not configured yet. Add openai_api_key in Streamlit Secrets."
        )
    elif not owner_authenticated():
        st.info("Unlock Owner controls in the sidebar to run the AI analysis.")
    else:
        run_col, info_col = st.columns([0.25, 0.75])
        with run_col:
            run_clicked = st.button(
                "Run AI Analysis",
                type="primary",
                use_container_width=True,
            )
        with info_col:
            st.caption(
                f"Sends {len(articles)} public-source headlines/snippets to "
                f"{secret_value('openai_model', DEFAULT_OPENAI_MODEL)}."
            )
        if run_clicked:
            try:
                with st.spinner("Filtering, clustering, and drafting the update…"):
                    # Clear prior AI editor widgets before storing the new result.
                    for key in list(st.session_state.keys()):
                        if key.startswith("ai_") and key != "ai_result":
                            del st.session_state[key]
                    st.session_state["ai_result"] = run_ai_analysis(articles)
                used_model = st.session_state.get(
                    "last_openai_model_used",
                    secret_value("openai_model", DEFAULT_OPENAI_MODEL),
                )
                usage = st.session_state.get("last_openai_usage", {})
                estimated_cost = st.session_state.get(
                    "last_openai_estimated_cost"
                )
                input_tokens = int(usage.get("input_tokens", 0) or 0)
                output_tokens = int(usage.get("output_tokens", 0) or 0)

                message = (
                    f"AI analysis completed using {used_model}. "
                    f"Tokens: {input_tokens:,} input and "
                    f"{output_tokens:,} output."
                )
                if estimated_cost is not None:
                    message += f" Estimated API cost: ${estimated_cost:.4f}."
                st.success(message)
            except Exception as exc:
                st.error(str(exc))

    if "ai_result" in st.session_state:
        relevant_clusters = [
            cluster
            for cluster in st.session_state["ai_result"]["clusters"]
            if cluster["relevant"]
        ]
        excluded_clusters = [
            cluster
            for cluster in st.session_state["ai_result"]["clusters"]
            if not cluster["relevant"]
        ]
        ai_items = [cluster_to_item(cluster, lookup) for cluster in relevant_clusters]
        initialize_ai_editor(ai_items)
        render_ai_review(ai_items)

        with st.expander(f"AI excluded {len(excluded_clusters)} irrelevant or weak items"):
            for cluster in excluded_clusters:
                primary = lookup.get(cluster["primary_article_id"])
                if primary:
                    st.markdown(f'**[{primary["title"]}]({primary["url"]})**')
                    st.caption(cluster.get("exclude_reason") or "Excluded as not sufficiently relevant.")

with preview_tab:
    using_ai = "ai_result" in st.session_state
    if using_ai:
        relevant_clusters = [
            cluster
            for cluster in st.session_state["ai_result"]["clusters"]
            if cluster["relevant"]
        ]
        ai_items = [cluster_to_item(cluster, lookup) for cluster in relevant_clusters]
        initialize_ai_editor(ai_items)
        final_briefing = build_ai_briefing(edited_ai_items(ai_items))
        st.success("Previewing the AI-cleaned and clustered draft.")
    else:
        final_briefing = selected_raw_briefing(raw_briefing)
        st.info("No AI analysis is active; previewing selected raw-feed items.")

    email_html = build_email_html(final_briefing, display_date)
    plain_text = build_plain_text(final_briefing, display_date)
    selected_count = sum(len(items) for items in final_briefing.values())
    st.caption(f"{selected_count} consolidated stories in the current update.")
    copy_buttons(email_html, plain_text)
    st.download_button(
        "Download HTML",
        data=email_html,
        file_name=f"news-update-{today.strftime('%Y-%m-%d')}.html",
        mime="text/html",
    )
    st.divider()
    st.html(email_html)

with raw_tab:
    render_raw_review(raw_briefing)

with status_tab:
    st.subheader("Source and OpenAI status")
    if source_errors:
        st.warning("Some source requests failed; the remaining results are still usable.")
        for error in source_errors:
            st.code(error)
    else:
        st.success("All configured news-source requests completed.")

    if secret_value("openai_api_key"):
        st.success("OpenAI API key is configured in Streamlit Secrets.")
    else:
        st.warning("OpenAI API key is not configured.")

    if secret_value("owner_password"):
        st.success("Owner-password protection is configured.")
    else:
        st.warning("Owner password is not configured.")

    st.markdown(
        f"""
        **Configured AI model:** `{secret_value('openai_model', DEFAULT_OPENAI_MODEL)}`

        **Current public sources**

        - Google News RSS searches
        - FederalRegister.gov public API

        AI analysis is stored only in the current browser session. Refreshing feeds or the app
        may clear it. Persistent daily archives and true “new since yesterday” storage are a
        later stage.
        """
    )
