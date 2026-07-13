import hashlib
import html
import json
import re
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
ITEMS_PER_SECTION = 7

SECTION_ORDER = [
    "Trump Administration Wins",
    "Top Developments",
    "UAS and Drones",
    "UAS Security",
    "eVTOL Integration Pilot Program and AAM",
    "Autonomous Vehicles",
    "Other Advanced Transportation",
    "Federal Actions",
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
        "TransportationNewsUpdate/1.0 "
        "(public-source Streamlit briefing; contact via repository)"
    )
}


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


def stable_id(section: str, url: str, title: str) -> str:
    raw = f"{section}|{url}|{title}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:14]


def google_news_url(query: str) -> str:
    timed_query = f"{query} when:{LOOKBACK_DAYS}d"
    encoded = quote_plus(timed_query)
    return (
        f"https://news.google.com/rss/search?q={encoded}"
        "&hl=en-US&gl=US&ceid=US:en"
    )


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_google_news(section: str, query: str) -> tuple[list[dict], str | None]:
    url = google_news_url(query)
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        return [], f"{section}: {exc}"

    feed = feedparser.parse(response.content)
    items = []

    for entry in feed.entries:
        title = clean_spaces(entry.get("title", "Untitled"))
        source = clean_spaces(
            getattr(entry.get("source", {}), "title", "")
            if not isinstance(entry.get("source", {}), dict)
            else entry.get("source", {}).get("title", "")
        )

        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)].strip()
        elif " - " in title:
            possible_title, possible_source = title.rsplit(" - ", 1)
            if 1 < len(possible_source) < 80:
                title = possible_title.strip()
                source = source or possible_source.strip()

        published = parse_rss_date(entry.get("published", ""))
        summary = strip_html(entry.get("summary", ""))

        # Google News summaries often just restate linked headlines.
        # Keep only a useful, non-repetitive snippet.
        if (
            len(summary) < 45
            or normalize_title(title) in normalize_title(summary)
            or summary.count("http") > 0
        ):
            summary = ""

        items.append(
            {
                "id": stable_id(section, entry.get("link", ""), title),
                "section": section,
                "title": title,
                "summary": summary,
                "source": source or "Google News",
                "url": entry.get("link", ""),
                "published": published.isoformat(),
                "date_label": published.strftime("%b. %d, %Y").replace(" 0", " "),
                "tag": (
                    "Candidate—verify Administration attribution"
                    if section == "Trump Administration Wins"
                    else section
                ),
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
                endpoint, params=params, headers=HEADERS, timeout=20
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
            if len(abstract) > 500:
                abstract = abstract[:497].rsplit(" ", 1)[0] + "…"

            url = result.get("html_url") or result.get("pdf_url") or ""

            items.append(
                {
                    "id": stable_id("Federal Actions", url, title),
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

    sorted_items = sorted(
        items,
        key=lambda item: item.get("published", ""),
        reverse=True,
    )

    for item in sorted_items:
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
    briefing = {}
    errors = []

    for section, query in NEWS_QUERIES.items():
        items, error = fetch_google_news(section, query)
        briefing[section] = deduplicate(items)[:ITEMS_PER_SECTION]
        if error:
            errors.append(error)

    federal_items, federal_errors = fetch_federal_register()
    briefing["Federal Actions"] = deduplicate(federal_items)[:ITEMS_PER_SECTION]
    errors.extend(federal_errors)

    main_sections = [
        "UAS and Drones",
        "UAS Security",
        "eVTOL Integration Pilot Program and AAM",
        "Autonomous Vehicles",
        "Other Advanced Transportation",
    ]

    candidates = []
    for section in main_sections:
        for item in briefing.get(section, [])[:2]:
            copied = dict(item)
            copied["section"] = "Top Developments"
            copied["tag"] = section

            # The same story may also remain in its original topic section.
            # Give the Top Developments copy its own widget identity so
            # Streamlit does not see duplicate checkbox/text-area keys.
            copied["id"] = stable_id(
                "Top Developments",
                copied.get("url", ""),
                copied.get("title", ""),
            )

            candidates.append(copied)

    briefing["Top Developments"] = deduplicate(candidates)[:ITEMS_PER_SECTION]

    for section in SECTION_ORDER:
        briefing.setdefault(section, [])

    return briefing, errors


def initialize_editor_state(briefing: dict[str, list[dict]]) -> None:
    for section in SECTION_ORDER:
        for item in briefing.get(section, []):
            include_key = f'include_{item["id"]}'
            summary_key = f'summary_{item["id"]}'

            if include_key not in st.session_state:
                # Candidate wins should be consciously approved.
                st.session_state[include_key] = (
                    section != "Trump Administration Wins"
                )
            if summary_key not in st.session_state:
                st.session_state[summary_key] = item.get("summary", "")


def selected_briefing(briefing: dict[str, list[dict]]) -> dict[str, list[dict]]:
    selected = {}

    for section in SECTION_ORDER:
        selected[section] = []
        for item in briefing.get(section, []):
            if st.session_state.get(f'include_{item["id"]}', False):
                copied = dict(item)
                copied["summary"] = st.session_state.get(
                    f'summary_{item["id"]}', ""
                ).strip()
                selected[section].append(copied)

    return selected


def safe_url(value: str) -> str:
    value = (value or "").strip()
    if value.startswith(("https://", "http://")):
        return html.escape(value, quote=True)
    return "#"


def article_html(item: dict) -> str:
    title = html.escape(item["title"])
    summary = html.escape(item.get("summary", ""))
    source = html.escape(item.get("source", "Source"))
    tag = html.escape(item.get("tag", ""))
    date_label = html.escape(item.get("date_label", ""))
    url = safe_url(item.get("url", ""))

    summary_markup = ""
    if summary:
        summary_markup = f"""
        <div style="font-size:15px;line-height:1.55;color:#222;margin-bottom:5px;">
          {summary}
        </div>
        """

    return f"""
    <div style="margin:0 0 18px 0;">
      <div style="font-size:17px;line-height:1.35;font-weight:700;margin-bottom:4px;">
        <a href="{url}" style="color:#153a66;text-decoration:none;">{title}</a>
      </div>
      {summary_markup}
      <div style="font-size:12px;line-height:1.4;color:#666;">
        {source}
        {f' &nbsp;•&nbsp; {date_label}' if date_label else ''}
        {f' &nbsp;•&nbsp; {tag}' if tag else ''}
        &nbsp;•&nbsp; <a href="{url}" style="color:#46698e;">Read source</a>
      </div>
    </div>
    """


def build_email_html(briefing: dict[str, list[dict]], display_date: str) -> str:
    sections = []

    for section_name in SECTION_ORDER:
        items = briefing.get(section_name, [])
        if not items:
            continue

        is_wins = section_name == "Trump Administration Wins"
        heading_color = "#8a1c1c" if is_wins else "#183a5a"
        border_color = "#b43232" if is_wins else "#d7dde4"
        background = "#fff8f4" if is_wins else "#ffffff"
        stories = "".join(article_html(item) for item in items)

        sections.append(
            f"""
            <div style="
                margin:0 0 26px 0;
                padding:{'18px' if is_wins else '0'};
                border:{'1px solid ' + border_color if is_wins else '0'};
                border-radius:{'8px' if is_wins else '0'};
                background:{background};
            ">
              <div style="
                  color:{heading_color};
                  font-size:20px;
                  line-height:1.3;
                  font-weight:800;
                  border-bottom:2px solid {border_color};
                  padding-bottom:7px;
                  margin-bottom:14px;
              ">
                {html.escape(section_name)}
              </div>
              {stories}
            </div>
            """
        )

    return f"""
    <div style="
        max-width:760px;
        margin:0 auto;
        padding:8px 4px 30px 4px;
        font-family:Arial,Helvetica,sans-serif;
        color:#1f2933;
        background:#ffffff;
    ">
      <div style="font-size:32px;line-height:1.2;font-weight:800;color:#132f4c;">
        News Update
      </div>
      <div style="font-size:15px;line-height:1.5;color:#5b6570;margin:5px 0 4px 0;">
        {html.escape(display_date)}
      </div>
      <div style="font-size:14px;line-height:1.5;color:#5b6570;margin-bottom:24px;">
        UAS, Advanced Transportation, and Airspace Policy
      </div>
      {''.join(sections)}
      <div style="
          margin-top:30px;
          padding-top:12px;
          border-top:1px solid #d7dde4;
          color:#777;
          font-size:11px;
          line-height:1.5;
      ">
        Public-source news update. Review all summaries, links, and Administration
        attributions before distribution.
      </div>
    </div>
    """


def build_plain_text(
    briefing: dict[str, list[dict]], display_date: str
) -> str:
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
            metadata = " | ".join(
                part
                for part in [
                    item.get("source", ""),
                    item.get("date_label", ""),
                    item.get("url", ""),
                ]
                if part
            )
            lines.append(metadata)
            lines.append("")

    lines.append(
        "Public-source news update. Review all summaries, links, and "
        "Administration attributions before distribution."
    )
    return "\n".join(lines)


def copy_buttons(email_html: str, plain_text: str) -> None:
    html_js = json.dumps(email_html)
    plain_js = json.dumps(plain_text)

    component = f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>
          body {{
            margin: 0;
            font-family: Arial, Helvetica, sans-serif;
            background: transparent;
          }}
          .row {{
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            align-items: center;
          }}
          button {{
            border: 1px solid #153a66;
            border-radius: 7px;
            padding: 10px 15px;
            font-size: 14px;
            font-weight: 700;
            cursor: pointer;
          }}
          #rich {{ background: #153a66; color: white; }}
          #plain {{ background: white; color: #153a66; }}
          #status {{
            color: #46606f;
            font-size: 13px;
            min-height: 18px;
          }}
        </style>
      </head>
      <body>
        <div class="row">
          <button id="rich" onclick="copyRich()">Copy for Email</button>
          <button id="plain" onclick="copyPlain()">Copy Plain Text</button>
          <span id="status"></span>
        </div>
        <script>
          const richContent = {html_js};
          const plainContent = {plain_js};

          function showStatus(message) {{
            const status = document.getElementById("status");
            status.textContent = message;
            setTimeout(() => status.textContent = "", 2500);
          }}

          async function copyRich() {{
            try {{
              const clipboardItem = new ClipboardItem({{
                "text/html": new Blob([richContent], {{type: "text/html"}}),
                "text/plain": new Blob([plainContent], {{type: "text/plain"}})
              }});
              await navigator.clipboard.write([clipboardItem]);
              showStatus("Copied with formatting.");
            }} catch (error) {{
              try {{
                await navigator.clipboard.writeText(plainContent);
                showStatus("Formatting was blocked; copied plain text.");
              }} catch (fallbackError) {{
                showStatus("Clipboard blocked by the browser.");
              }}
            }}
          }}

          async function copyPlain() {{
            try {{
              await navigator.clipboard.writeText(plainContent);
              showStatus("Plain text copied.");
            }} catch (error) {{
              showStatus("Clipboard blocked by the browser.");
            }}
          }}
        </script>
      </body>
    </html>
    """
    components.html(component, height=60)


def render_review_section(section: str, items: list[dict]) -> None:
    st.subheader(section)

    if section == "Trump Administration Wins":
        st.info(
            "These are candidates found through executive-order-related searches. "
            "They are excluded from the email until you affirmatively select them."
        )

    if not items:
        st.caption("No matching items were found during this refresh.")
        return

    for item in items:
        include_key = f'include_{item["id"]}'
        summary_key = f'summary_{item["id"]}'

        with st.container(border=True):
            left, right = st.columns([0.76, 0.24])
            with left:
                st.markdown(f'**[{item["title"]}]({item["url"]})**')
                st.caption(
                    " · ".join(
                        part
                        for part in [
                            item.get("source", ""),
                            item.get("date_label", ""),
                            item.get("origin", ""),
                        ]
                        if part
                    )
                )
            with right:
                st.checkbox(
                    "Include in update",
                    key=include_key,
                )

            st.text_area(
                "Summary or editorial note",
                key=summary_key,
                height=88,
                placeholder=(
                    "Add a concise summary. Leave blank to include only the "
                    "headline, source, date, and link."
                ),
                label_visibility="collapsed",
            )


# -----------------------------
# PAGE
# -----------------------------
st.markdown(
    """
    <style>
      .block-container {
        max-width: 1180px;
        padding-top: 1.5rem;
        padding-bottom: 4rem;
      }
      [data-testid="stHeader"] {
        background: rgba(255,255,255,0.92);
      }
    </style>
    """,
    unsafe_allow_html=True,
)

today = datetime.now(EASTERN)
display_date = today.strftime("%A, %B %d, %Y").replace(" 0", " ")

st.title("News Update")
st.caption(
    f"Live public-source prototype · preceding {LOOKBACK_DAYS} days · "
    f"last page run {today.strftime('%-I:%M %p')} Eastern"
)

button_col, note_col = st.columns([0.22, 0.78])
with button_col:
    if st.button("Refresh live feeds", use_container_width=True):
        st.cache_data.clear()
        st.session_state.clear()
        st.rerun()
with note_col:
    st.caption(
        "The first load may take several seconds. Feed results are cached for one hour."
    )

briefing, errors = load_all_news()
initialize_editor_state(briefing)

review_tab, preview_tab, status_tab = st.tabs(
    ["1. Review and Edit", "2. Email Preview", "Source Status"]
)

with review_tab:
    st.markdown(
        "Select the items to include and add or revise a short summary. "
        "Changes immediately appear in **Email Preview**."
    )
    for section in SECTION_ORDER:
        render_review_section(section, briefing.get(section, []))
        st.divider()

with preview_tab:
    selected = selected_briefing(briefing)
    email_html = build_email_html(selected, display_date)
    plain_text = build_plain_text(selected, display_date)

    selected_count = sum(len(items) for items in selected.values())
    st.caption(f"{selected_count} items selected for the current update.")
    copy_buttons(email_html, plain_text)
    st.download_button(
        "Download HTML",
        data=email_html,
        file_name=f"news-update-{today.strftime('%Y-%m-%d')}.html",
        mime="text/html",
    )
    st.divider()
    st.html(email_html)

with status_tab:
    st.subheader("Source status")
    if errors:
        st.warning(
            "Some source requests failed. The rest of the briefing can still be used."
        )
        for error in errors:
            st.code(error)
    else:
        st.success("All configured source requests completed.")

    st.markdown(
        """
        **Current free sources**

        - Google News RSS searches for the five portfolio topics and executive-order terms
        - FederalRegister.gov public API for recent federal documents

        This version does not yet remember yesterday's stories permanently. It uses
        a rolling two-day window and removes duplicates within the current result set.
        Persistent “new since the previous morning” tracking is the next stage.
        """
    )
