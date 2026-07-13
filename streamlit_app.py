import html
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit.components.v1 as components


st.set_page_config(
    page_title="News Update",
    page_icon="📰",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# -------------------------------------------------------------------
# PROTOTYPE DATA
# These sample items demonstrate the format only. In the next stage,
# this dictionary will be populated automatically from news sources.
# -------------------------------------------------------------------
BRIEFING = {
    "Trump Administration Wins": [
        {
            "title": "Sample Administration win — replace with a live item",
            "summary": (
                "This space will highlight a verified policy, regulatory, "
                "security, manufacturing, or operational achievement tied to "
                "the Trump Administration's advanced-transportation agenda."
            ),
            "source": "Official source",
            "url": "https://www.whitehouse.gov/",
            "tag": "EO connection to be verified",
        }
    ],
    "Top Developments": [
        {
            "title": "Sample top development — replace with a live item",
            "summary": (
                "The most consequential new developments across the portfolio "
                "will appear here, regardless of their topic."
            ),
            "source": "News source",
            "url": "https://www.transportation.gov/",
            "tag": "Top development",
        }
    ],
    "UAS and Drones": [
        {
            "title": "Sample UAS item",
            "summary": "A concise two- or three-sentence summary will appear here.",
            "source": "FAA",
            "url": "https://www.faa.gov/uas",
            "tag": "UAS",
        }
    ],
    "UAS Security": [
        {
            "title": "Sample UAS security item",
            "summary": (
                "Counter-UAS, critical-infrastructure protection, airspace "
                "security, enforcement, and related developments will appear here."
            ),
            "source": "DHS",
            "url": "https://www.dhs.gov/",
            "tag": "UAS security",
        }
    ],
    "eVTOL Integration Pilot Program and AAM": [
        {
            "title": "Sample eIPP or AAM item",
            "summary": (
                "New eIPP milestones, powered-lift integration, and advanced-air-"
                "mobility developments will appear here."
            ),
            "source": "FAA",
            "url": "https://www.faa.gov/air-taxis",
            "tag": "eIPP / AAM",
        }
    ],
    "Autonomous Vehicles": [
        {
            "title": "Sample autonomous-vehicle item",
            "summary": (
                "Federal actions, state developments, deployments, safety data, "
                "and industry milestones will appear here."
            ),
            "source": "NHTSA",
            "url": "https://www.nhtsa.gov/vehicle-safety/automated-vehicles-safety",
            "tag": "Autonomous vehicles",
        }
    ],
    "Other Advanced Transportation": [
        {
            "title": "Sample civil-supersonics, rail, or emerging-technology item",
            "summary": (
                "Civil supersonics, high-speed rail, maglev, autonomous rail, "
                "and other advanced transportation developments will appear here."
            ),
            "source": "DOT",
            "url": "https://www.transportation.gov/",
            "tag": "Advanced transportation",
        }
    ],
    "Federal Actions": [
        {
            "title": "Sample federal action",
            "summary": (
                "Federal Register documents, agency announcements, rules, notices, "
                "guidance, grants, waivers, procurements, and hearings will appear here."
            ),
            "source": "Federal Register",
            "url": "https://www.federalregister.gov/",
            "tag": "Federal action",
        }
    ],
}


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


def safe_url(value: str) -> str:
    """Allow only ordinary HTTP(S) links in the rendered briefing."""
    value = value.strip()
    if value.startswith(("https://", "http://")):
        return html.escape(value, quote=True)
    return "#"


def article_html(item: dict) -> str:
    title = html.escape(item["title"])
    summary = html.escape(item["summary"])
    source = html.escape(item["source"])
    tag = html.escape(item.get("tag", ""))
    url = safe_url(item["url"])

    return f"""
    <div style="margin:0 0 18px 0;">
      <div style="font-size:17px;line-height:1.35;font-weight:700;margin-bottom:4px;">
        <a href="{url}" style="color:#153a66;text-decoration:none;">{title}</a>
      </div>
      <div style="font-size:15px;line-height:1.55;color:#222;margin-bottom:5px;">
        {summary}
      </div>
      <div style="font-size:12px;line-height:1.4;color:#666;">
        {source} &nbsp;•&nbsp; {tag} &nbsp;•&nbsp;
        <a href="{url}" style="color:#46698e;">Read source</a>
      </div>
    </div>
    """


def build_email_html(briefing: dict, display_date: str) -> str:
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
        Public-source news update. Review all summaries and attributions before distribution.
      </div>
    </div>
    """


def build_plain_text(briefing: dict, display_date: str) -> str:
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
            lines.append(item["summary"])
            lines.append(f'{item["source"]} | {item["url"]}')
            lines.append("")

    lines.append(
        "Public-source news update. Review all summaries and attributions before distribution."
    )
    return "\n".join(lines)


def copy_buttons(email_html: str, plain_text: str) -> None:
    """Render clipboard buttons in an isolated Streamlit component."""
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
          #rich {{
            background: #153a66;
            color: white;
          }}
          #plain {{
            background: white;
            color: #153a66;
          }}
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


# -----------------------------
# PAGE
# -----------------------------
today = datetime.now(ZoneInfo("America/New_York"))
display_date = today.strftime("%A, %B %d, %Y").replace(" 0", " ")

email_html = build_email_html(BRIEFING, display_date)
plain_text = build_plain_text(BRIEFING, display_date)

st.markdown(
    """
    <style>
      .block-container {
        max-width: 900px;
        padding-top: 2rem;
        padding-bottom: 4rem;
      }
      [data-testid="stHeader"] {
        background: rgba(255,255,255,0.92);
      }
    </style>
    """,
    unsafe_allow_html=True,
)

st.caption("Prototype · public-source sample data")
copy_buttons(email_html, plain_text)

st.download_button(
    "Download HTML",
    data=email_html,
    file_name=f"news-update-{today.strftime('%Y-%m-%d')}.html",
    mime="text/html",
)

st.divider()
st.html(email_html)

with st.expander("What comes next"):
    st.markdown(
        """
        1. Replace the sample stories with automatically collected articles.
        2. Add duplicate detection and topic classification.
        3. Add an editorial review screen.
        4. Add automatic executive-order mapping and Administration-win review.
        5. Schedule the collector to refresh before the morning briefing.
        """
    )
