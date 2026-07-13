from __future__ import annotations

import hmac
import html
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit.components.v1 as components

from news_engine import (
    DEFAULT_OPENAI_MODEL,
    EASTERN,
    EO_DISPLAY_NAMES,
    SECTION_ORDER,
    TOPIC_SECTIONS,
    generate_daily_briefing,
)

st.set_page_config(
    page_title="News Update",
    page_icon="📰",
    layout="centered",
    initial_sidebar_state="collapsed",
)

ROOT = Path(__file__).resolve().parent
LATEST_PATH = ROOT / "data" / "latest_briefing.json"
ARCHIVE_DIR = ROOT / "data" / "archive"


def secret_value(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return default


def owner_authenticated() -> bool:
    return bool(st.session_state.get("owner_authenticated", False))


def render_owner_access() -> None:
    st.sidebar.header("Owner controls")
    expected = secret_value("owner_password")
    if owner_authenticated():
        st.sidebar.success("Unlocked")
        if st.sidebar.button("Lock"):
            st.session_state["owner_authenticated"] = False
            st.rerun()
        return

    password = st.sidebar.text_input("Owner password", type="password")
    if st.sidebar.button("Unlock"):
        if expected and hmac.compare_digest(password, expected):
            st.session_state["owner_authenticated"] = True
            st.rerun()
        else:
            st.sidebar.error("Incorrect password.")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def available_editions() -> list[Path]:
    paths = sorted(ARCHIVE_DIR.glob("*.json"), reverse=True) if ARCHIVE_DIR.exists() else []
    if LATEST_PATH.exists() and LATEST_PATH not in paths:
        paths.insert(0, LATEST_PATH)
    return paths


def edition_label(path: Path) -> str:
    if path.name == "latest_briefing.json":
        return "Latest daily edition"
    return path.stem


def safe_url(value: str) -> str:
    value = (value or "").strip()
    if value.startswith(("https://", "http://")):
        return html.escape(value, quote=True)
    return "#"


def format_datetime(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value).astimezone(EASTERN)
        return dt.strftime("%B %d, %Y at %-I:%M %p ET").replace(" 0", " ")
    except (ValueError, TypeError):
        return value


def eo_display(item: dict) -> str:
    number = item.get("eo_number", "")
    name = item.get("eo_name", "") or EO_DISPLAY_NAMES.get(number, "")
    section = item.get("eo_section", "")
    citation = ", ".join(part for part in [number, section] if part)
    if name and citation:
        return f"{name} ({citation})"
    return name or citation


def related_sources(item: dict) -> list[dict]:
    unique = []
    seen = {item.get("source", "").casefold()}
    for related in item.get("also_covered", []):
        source = related.get("source", "").strip()
        if not source or source.casefold() in seen:
            continue
        seen.add(source.casefold())
        unique.append(related)
    return unique


def article_html(item: dict, section_name: str) -> str:
    title = html.escape(item.get("title", "Untitled"))
    summary = html.escape(item.get("summary", ""))
    source = html.escape(item.get("source", "Source"))
    date_label = html.escape(item.get("date_label", ""))
    url = safe_url(item.get("url", ""))

    category = ""
    if section_name == "Top Developments" and item.get("section"):
        category = (
            '<div style="font-size:10px;font-weight:700;color:#48657d;'
            'text-transform:uppercase;letter-spacing:.4px;margin-bottom:4px;">'
            + html.escape(item["section"])
            + "</div>"
        )

    win = ""
    if item.get("is_administration_win") and item.get("win_explanation"):
        citation = eo_display(item)
        citation_html = (
            f'<div style="font-size:12px;font-weight:700;color:#8a2e27;'
            f'margin-bottom:4px;">{html.escape(citation)}</div>'
            if citation else ""
        )
        win = f"""
        <div style="background:#fff3ef;border-left:3px solid #b42318;
            padding:9px 11px;margin:9px 0;">
          <div style="font-size:11px;font-weight:800;color:#7a271a;
              letter-spacing:.25px;margin-bottom:3px;">
            WHY THIS IS A TRUMP ADMINISTRATION WIN
          </div>
          {citation_html}
          <div style="font-size:14px;line-height:1.45;color:#57201b;">
            {html.escape(item['win_explanation'])}
          </div>
        </div>
        """

    related = related_sources(item)
    related_html = ""
    if related:
        shown = related[:3]
        links = [
            f'<a href="{safe_url(rel.get("url", ""))}" '
            f'style="color:#61778b;text-decoration:underline;">'
            f'{html.escape(rel.get("source", "Related coverage"))}</a>'
            for rel in shown
        ]
        extra = len(related) - len(shown)
        suffix = f" · +{extra} more" if extra else ""
        related_html = (
            '<div style="font-size:11px;line-height:1.4;color:#78838c;margin-top:5px;">'
            '<strong>Also covered by:</strong> ' + " · ".join(links) + suffix + "</div>"
        )

    return f"""
    <div style="padding:0 0 14px 0;margin:0 0 14px 0;
        border-bottom:1px solid #e1e6ea;">
      {category}
      <div style="font-size:17px;line-height:1.32;font-weight:700;margin-bottom:5px;">
        <a href="{url}" style="color:#173c5e;text-decoration:none;">{title}</a>
      </div>
      <div style="font-size:14px;line-height:1.5;color:#252b31;margin-bottom:6px;">
        {summary}
      </div>
      {win}
      <div style="font-size:11px;line-height:1.4;color:#707b84;">
        {source}{f' &nbsp;•&nbsp; {date_label}' if date_label else ''}
        &nbsp;•&nbsp; <a href="{url}" style="color:#58738a;">Read source</a>
      </div>
      {related_html}
    </div>
    """


def section_html(title: str, items: list[dict]) -> str:
    if not items:
        return ""
    is_wins = title == "Trump Administration Wins"
    heading_color = "#8c241e" if is_wins else "#173c5e"
    content = "".join(article_html(item, title) for item in items)
    return f"""
    <div style="margin:0 0 25px 0;">
      <div style="font-size:19px;line-height:1.3;font-weight:800;color:{heading_color};
          padding-bottom:6px;margin-bottom:12px;border-bottom:2px solid
          {'#b42318' if is_wins else '#cbd6de'};">
        {html.escape(title)}
      </div>
      {content}
    </div>
    """


def build_email_html(briefing: dict, executive_only: bool = False) -> str:
    end = datetime.fromisoformat(briefing["window_end"]).astimezone(EASTERN)
    date_text = end.strftime("%A, %B %d, %Y").replace(" 0", " ")
    start_text = format_datetime(briefing.get("window_start", ""))
    end_text = format_datetime(briefing.get("window_end", ""))

    sections = briefing.get("sections", {})
    visible_sections = ["Trump Administration Wins", "Top Developments"]
    if not executive_only:
        visible_sections.extend(TOPIC_SECTIONS)

    section_markup = "".join(
        section_html(section, sections.get(section, []))
        for section in visible_sections
    )

    watch = briefing.get("what_to_watch", [])
    watch_markup = ""
    if watch:
        list_items = "".join(
            f'<li style="margin:0 0 5px 0;">{html.escape(item)}</li>'
            for item in watch
        )
        watch_markup = f"""
        <div style="margin:0 0 24px 0;">
          <div style="font-size:19px;font-weight:800;color:#173c5e;
              border-bottom:2px solid #cbd6de;padding-bottom:6px;margin-bottom:10px;">
            What to Watch
          </div>
          <ul style="margin:0;padding-left:20px;font-size:14px;line-height:1.5;color:#252b31;">
            {list_items}
          </ul>
        </div>
        """

    return f"""
    <div style="max-width:740px;margin:0 auto;background:#ffffff;
        font-family:Arial,Helvetica,sans-serif;color:#1f2933;">
      <div style="background:#153a5a;padding:19px 21px 17px 21px;">
        <div style="font-size:29px;line-height:1.15;font-weight:800;color:#ffffff;">
          News Update
        </div>
        <div style="font-size:14px;line-height:1.45;color:#dce8f0;margin-top:5px;">
          {html.escape(date_text)}
        </div>
        <div style="font-size:12px;line-height:1.45;color:#dce8f0;">
          UAS, C-UAS, and Advanced Transportation
        </div>
      </div>

      <div style="font-size:10px;line-height:1.4;color:#687681;
          padding:8px 2px 12px 2px;">
        24-HOUR COVERAGE WINDOW: {html.escape(start_text)} through {html.escape(end_text)}
      </div>

      <div style="background:#edf4f8;border-left:3px solid #4d7898;
          padding:12px 14px;margin:0 0 23px 0;">
        <div style="font-size:12px;font-weight:800;color:#244d6b;
            letter-spacing:.25px;margin-bottom:4px;">EXECUTIVE SUMMARY</div>
        <div style="font-size:15px;line-height:1.5;color:#24323d;">
          {html.escape(briefing.get('executive_summary', ''))}
        </div>
      </div>

      {section_markup}
      {watch_markup}

      <div style="font-size:10px;line-height:1.45;color:#7b848c;
          border-top:1px solid #dfe4e8;padding-top:9px;margin-top:22px;">
        Public-source, AI-assisted news update. Review summaries, links,
        executive-order citations, and Administration attributions before distribution.
      </div>
    </div>
    """


def build_plain_text(briefing: dict, executive_only: bool = False) -> str:
    end = datetime.fromisoformat(briefing["window_end"]).astimezone(EASTERN)
    lines = [
        "NEWS UPDATE",
        end.strftime("%A, %B %d, %Y").replace(" 0", " "),
        "UAS, C-UAS, AND ADVANCED TRANSPORTATION",
        f"24-hour coverage: {format_datetime(briefing['window_start'])} through {format_datetime(briefing['window_end'])}",
        "",
        "EXECUTIVE SUMMARY",
        briefing.get("executive_summary", ""),
        "",
    ]

    sections = briefing.get("sections", {})
    visible = ["Trump Administration Wins", "Top Developments"]
    if not executive_only:
        visible.extend(TOPIC_SECTIONS)

    for section in visible:
        items = sections.get(section, [])
        if not items:
            continue
        lines.extend([section.upper(), ""])
        for item in items:
            lines.append(item.get("title", ""))
            lines.append(item.get("summary", ""))
            if item.get("win_explanation"):
                lines.append("WHY THIS IS A TRUMP ADMINISTRATION WIN")
                citation = eo_display(item)
                if citation:
                    lines.append(citation)
                lines.append(item["win_explanation"])
            lines.append(
                " | ".join(
                    value for value in [
                        item.get("source", ""),
                        item.get("date_label", ""),
                        item.get("url", ""),
                    ] if value
                )
            )
            related = related_sources(item)
            if related:
                lines.append("Also covered by: " + " | ".join(
                    f"{rel.get('source', '')}: {rel.get('url', '')}" for rel in related[:3]
                ))
            lines.append("")

    watch = briefing.get("what_to_watch", [])
    if watch:
        lines.extend(["WHAT TO WATCH", ""])
        lines.extend(f"• {item}" for item in watch)
        lines.append("")
    return "\n".join(lines)


def copy_controls(full_html: str, full_text: str, short_html: str, short_text: str) -> None:
    component = f"""
    <!doctype html><html><head><meta charset="utf-8"><style>
    body{{margin:0;font-family:Arial,Helvetica,sans-serif;background:transparent}}
    .row{{display:flex;gap:9px;align-items:center;flex-wrap:wrap}}
    button{{border:1px solid #153a5a;border-radius:6px;padding:10px 14px;
      font-size:13px;font-weight:700;cursor:pointer}}
    #full{{background:#153a5a;color:white}} #short{{background:white;color:#153a5a}}
    #status{{font-size:12px;color:#5f6f7b;min-height:17px}}
    </style></head><body><div class="row">
      <button id="full" onclick="copyVersion('full')">Copy for Outlook</button>
      <button id="short" onclick="copyVersion('short')">Copy Executive Version</button>
      <span id="status"></span>
    </div><script>
    const fullHtml={json.dumps(full_html)}; const fullText={json.dumps(full_text)};
    const shortHtml={json.dumps(short_html)}; const shortText={json.dumps(short_text)};
    function status(msg){{const el=document.getElementById('status');el.textContent=msg;
      setTimeout(()=>el.textContent='',2600)}}
    async function copyVersion(which){{
      const h=which==='full'?fullHtml:shortHtml; const t=which==='full'?fullText:shortText;
      try{{await navigator.clipboard.write([new ClipboardItem({{
        'text/html':new Blob([h],{{type:'text/html'}}),
        'text/plain':new Blob([t],{{type:'text/plain'}})}})]);status('Copied with formatting.')}}
      catch(e){{try{{await navigator.clipboard.writeText(t);status('Copied as plain text.')}}
      catch(e2){{status('Browser blocked clipboard access.')}}}}
    }}
    </script></body></html>
    """
    components.html(component, height=55)


def initialize_editor(briefing: dict, edition_key: str) -> None:
    prefix = f"edit_{edition_key}_"
    if prefix + "initialized" in st.session_state:
        return
    st.session_state[prefix + "executive_summary"] = briefing.get("executive_summary", "")
    st.session_state[prefix + "what_to_watch"] = "\n".join(briefing.get("what_to_watch", []))
    for section, items in briefing.get("sections", {}).items():
        for item in items:
            item_id = item["id"]
            st.session_state[prefix + item_id + "_include"] = True
            st.session_state[prefix + item_id + "_title"] = item.get("title", "")
            st.session_state[prefix + item_id + "_summary"] = item.get("summary", "")
            st.session_state[prefix + item_id + "_win"] = item.get("win_explanation", "")
    st.session_state[prefix + "initialized"] = True


def edited_briefing(briefing: dict, edition_key: str) -> dict:
    prefix = f"edit_{edition_key}_"
    edited = json.loads(json.dumps(briefing))
    edited["executive_summary"] = st.session_state.get(
        prefix + "executive_summary", briefing.get("executive_summary", "")
    ).strip()
    edited["what_to_watch"] = [
        line.strip() for line in st.session_state.get(prefix + "what_to_watch", "").splitlines()
        if line.strip()
    ][:3]
    for section, items in list(edited.get("sections", {}).items()):
        kept = []
        for item in items:
            item_id = item["id"]
            if not st.session_state.get(prefix + item_id + "_include", True):
                continue
            item["title"] = st.session_state.get(prefix + item_id + "_title", item["title"]).strip()
            item["summary"] = st.session_state.get(prefix + item_id + "_summary", item["summary"]).strip()
            item["win_explanation"] = st.session_state.get(prefix + item_id + "_win", item.get("win_explanation", "")).strip()
            kept.append(item)
        edited["sections"][section] = kept
    return edited


def render_editor(briefing: dict, edition_key: str) -> None:
    prefix = f"edit_{edition_key}_"
    st.text_area("Executive Summary", key=prefix + "executive_summary", height=110)
    st.text_area(
        "What to Watch — one item per line",
        key=prefix + "what_to_watch",
        height=95,
    )
    for section in SECTION_ORDER:
        items = briefing.get("sections", {}).get(section, [])
        if not items:
            continue
        st.subheader(section)
        for item in items:
            item_id = item["id"]
            with st.container(border=True):
                st.checkbox("Include", key=prefix + item_id + "_include")
                st.text_input("Headline", key=prefix + item_id + "_title")
                st.text_area("Summary", key=prefix + item_id + "_summary", height=85)
                if item.get("is_administration_win"):
                    st.text_area(
                        "Why this is a Trump Administration win",
                        key=prefix + item_id + "_win",
                        height=90,
                    )
                st.caption(f"{item.get('source', '')} · {item.get('date_label', '')}")


st.markdown("""
<style>
.block-container{max-width:900px;padding-top:1.4rem;padding-bottom:4rem}
[data-testid="stHeader"]{background:rgba(255,255,255,.94)}
</style>
""", unsafe_allow_html=True)

render_owner_access()
paths = available_editions()

if not paths:
    st.error("No saved daily briefing exists yet. Run the GitHub Action or use Run now after unlocking.")
    briefing = None
    edition_key = "temporary"
else:
    labels = {edition_label(path): path for path in paths}
    selected_label = st.selectbox("Edition", list(labels), label_visibility="collapsed")
    selected_path = labels[selected_label]
    briefing = load_json(selected_path)
    edition_key = selected_path.stem

if owner_authenticated():
    st.sidebar.divider()
    st.sidebar.caption("Run now creates a temporary edition for this browser session. The scheduled GitHub Action creates the permanent daily edition.")
    if st.sidebar.button("Run AI analysis now", use_container_width=True):
        api_key = secret_value("openai_api_key")
        model = secret_value("openai_model", DEFAULT_OPENAI_MODEL)
        if not api_key:
            st.sidebar.error("Add openai_api_key to Streamlit Secrets.")
        else:
            with st.spinner("Collecting and analyzing the preceding 24 hours…"):
                try:
                    st.session_state["temporary_briefing"] = generate_daily_briefing(api_key, model)
                    st.session_state["use_temporary"] = True
                    st.rerun()
                except Exception as exc:
                    st.sidebar.error(str(exc))

if st.session_state.get("use_temporary") and st.session_state.get("temporary_briefing"):
    briefing = st.session_state["temporary_briefing"]
    edition_key = "temporary_" + briefing.get("generated_at", "now")
    st.info("Showing a temporary manual run. The next scheduled GitHub Action will create the permanent saved edition.")
    if st.button("Return to saved daily edition"):
        st.session_state["use_temporary"] = False
        st.rerun()

if briefing is None:
    st.stop()

if not briefing.get("window_end"):
    st.warning(
        "The first scheduled edition has not been generated yet. Open the GitHub "
        "Actions tab and run Daily Transportation News Update, or unlock Owner "
        "controls and use Run AI analysis now."
    )
    st.stop()

initialize_editor(briefing, edition_key)
current = edited_briefing(briefing, edition_key) if owner_authenticated() else briefing

end = datetime.fromisoformat(current["window_end"]).astimezone(EASTERN)
st.title("News Update")
st.caption(
    f"24-hour coverage through {end.strftime('%-I:%M %p ET on %B %d, %Y').replace(' 0', ' ')}"
)

preview_tab, edit_tab, status_tab = st.tabs(["Email Preview", "Review & Edit", "Status"])

with preview_tab:
    full_html = build_email_html(current, executive_only=False)
    full_text = build_plain_text(current, executive_only=False)
    short_html = build_email_html(current, executive_only=True)
    short_text = build_plain_text(current, executive_only=True)
    copy_controls(full_html, full_text, short_html, short_text)
    st.download_button(
        "Download HTML",
        data=full_html,
        file_name=f"news-update-{end.date().isoformat()}.html",
        mime="text/html",
    )
    st.divider()
    st.html(full_html)

with edit_tab:
    if owner_authenticated():
        st.caption("Edits affect only this browser session and the copied email; they do not modify the archived GitHub edition.")
        render_editor(briefing, edition_key)
    else:
        st.info("Unlock Owner controls in the sidebar to edit the briefing.")

with status_tab:
    st.write(f"**Generated:** {format_datetime(current.get('generated_at', ''))}")
    st.write(f"**Coverage:** {format_datetime(current['window_start'])} through {format_datetime(current['window_end'])}")
    st.write(f"**Model:** {current.get('model', '')}")
    usage = current.get("usage", {})
    if usage:
        st.write(
            f"**Tokens:** {int(usage.get('input_tokens', 0)):,} input; "
            f"{int(usage.get('output_tokens', 0)):,} output"
        )
    if current.get("estimated_cost") is not None:
        st.write(f"**Estimated API cost:** ${current['estimated_cost']:.4f}")
    st.write(f"**Candidate records reviewed:** {current.get('candidate_count', 0)}")
    if current.get("source_errors"):
        st.warning("Some source requests failed:")
        for error in current["source_errors"]:
            st.code(error)
    else:
        st.success("All configured source requests completed.")
