from __future__ import annotations

import hmac
import html
import ipaddress
import json
import random
import re
import socket
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
import streamlit as st
import streamlit.components.v1 as components

from news_engine import (
    DEFAULT_OPENAI_MODEL,
    EASTERN,
    EO_DISPLAY_NAMES,
    SECTION_ORDER,
    TOPIC_SECTIONS,
    clean_spaces,
    generate_briefing_from_records,
    stable_id,
)

st.set_page_config(
    page_title="Advanced Transportation News Update",
    page_icon="📰",
    layout="centered",
    initial_sidebar_state="collapsed",
)

ROOT = Path(__file__).resolve().parent
LATEST_RAW_PATH = ROOT / "data" / "latest_raw_news.json"
RAW_ARCHIVE_DIR = ROOT / "data" / "raw_archive"


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



def compact_article_html(item: dict) -> str:
    title = html.escape(item.get("title", "Untitled"))
    source = html.escape(item.get("source", "Source"))
    date_label = html.escape(item.get("date_label", ""))
    url = safe_url(item.get("url", ""))
    related = related_sources(item)
    related_note = ""
    if related:
        related_note = f" · also {len(related)} other outlet{'s' if len(related) != 1 else ''}"
    return f"""
    <div style="padding:0 0 8px 0;margin:0 0 8px 0;">
      <div style="font-size:14px;line-height:1.35;">
        <a href="{url}" style="color:#173c5e;text-decoration:none;font-weight:700;">{title}</a>
      </div>
      <div style="font-size:10px;line-height:1.35;color:#77818a;">
        {source}{f' &nbsp;•&nbsp; {date_label}' if date_label else ''}{related_note}
      </div>
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


def build_web_preview_html(briefing: dict, executive_only: bool = False) -> str:
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
          Advanced Transportation News Update
        </div>
        <div style="font-size:14px;line-height:1.45;color:#dce8f0;margin-top:5px;">
          {html.escape(date_text)}
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



def outlook_spacer(height: int) -> str:
    return (
        f'<tr><td height="{height}" style="height:{height}px;'
        f'line-height:{height}px;font-size:0;">&nbsp;</td></tr>'
    )


def outlook_related_html(item: dict) -> str:
    related = related_sources(item)
    if not related:
        return ""

    shown = related[:3]
    links = []
    for related_item in shown:
        links.append(
            f'<a href="{safe_url(related_item.get("url", ""))}" '
            f'style="color:#60758a;text-decoration:underline;">'
            f'{html.escape(related_item.get("source", "Related coverage"))}</a>'
        )

    extra = len(related) - len(shown)
    suffix = f" &nbsp;•&nbsp; +{extra} more" if extra else ""

    return f"""
    <tr>
      <td style="padding:8px 0 0 0;font-family:Arial,Helvetica,sans-serif;
          font-size:10px;line-height:15px;color:#737f89;
          mso-line-height-rule:exactly;">
        <strong style="color:#596873;">Additional coverage:</strong>
        {" &nbsp;•&nbsp; ".join(links)}{suffix}
      </td>
    </tr>
    """


def outlook_story_html(item: dict, section_name: str, compact: bool = False) -> str:
    title = html.escape(item.get("title", "Untitled"))
    summary = html.escape(item.get("summary", ""))
    source = html.escape(item.get("source", "Source"))
    date_label = html.escape(item.get("date_label", ""))
    url = safe_url(item.get("url", ""))

    category_html = ""
    if section_name == "Top Developments" and item.get("section"):
        category_html = f"""
        <tr>
          <td style="padding:0 0 5px 0;font-family:Arial,Helvetica,sans-serif;
              font-size:9px;line-height:12px;font-weight:bold;color:#49677f;
              text-transform:uppercase;letter-spacing:.4px;
              mso-line-height-rule:exactly;">
            {html.escape(item["section"])}
          </td>
        </tr>
        """

    if compact:
        related = related_sources(item)
        related_note = (
            f" &nbsp;•&nbsp; also {len(related)} other outlet"
            f"{'s' if len(related) != 1 else ''}"
            if related else ""
        )
        return f"""
        <table role="presentation" width="100%" border="0" cellspacing="0"
            cellpadding="0" style="width:100%;border-collapse:collapse;">
          <tr>
            <td width="14" valign="top" style="width:14px;padding:2px 0 0 0;
                font-family:Arial,Helvetica,sans-serif;font-size:13px;
                line-height:18px;color:#49677f;">&#8226;</td>
            <td valign="top" style="padding:0 0 9px 0;
                font-family:Arial,Helvetica,sans-serif;">
              <div style="font-size:13px;line-height:18px;font-weight:bold;
                  mso-line-height-rule:exactly;">
                <a href="{url}" style="color:#173c5e;text-decoration:underline;">
                  {title}
                </a>
              </div>
              <div style="padding-top:2px;font-size:9px;line-height:13px;
                  color:#78838c;mso-line-height-rule:exactly;">
                {source}{f' &nbsp;•&nbsp; {date_label}' if date_label else ''}
                {related_note}
              </div>
            </td>
          </tr>
        </table>
        """

    win_html = ""
    if item.get("is_administration_win") and item.get("win_explanation"):
        citation = html.escape(eo_display(item))
        win_html = f"""
        <tr>
          <td style="padding:11px 0 3px 0;">
            <table role="presentation" width="100%" border="0" cellspacing="0"
                cellpadding="0" bgcolor="#FFF2EE"
                style="width:100%;border-collapse:collapse;background-color:#FFF2EE;
                border-left:4px solid #B42318;">
              <tr>
                <td style="padding:12px 14px 13px 14px;
                    font-family:Arial,Helvetica,sans-serif;">
                  <div style="font-size:10px;line-height:14px;font-weight:bold;
                      color:#7A271A;letter-spacing:.3px;
                      mso-line-height-rule:exactly;">
                    WHY THIS IS A TRUMP ADMINISTRATION WIN
                  </div>
                  {f'<div style="padding-top:4px;font-size:11px;line-height:16px;'
                    f'font-weight:bold;color:#8A2E27;mso-line-height-rule:exactly;">'
                    f'{citation}</div>' if citation else ''}
                  <div style="padding-top:5px;font-size:13px;line-height:20px;
                      color:#57201B;mso-line-height-rule:exactly;">
                    {html.escape(item["win_explanation"])}
                  </div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        """

    summary_html = ""
    if summary:
        summary_html = f"""
        <tr>
          <td style="padding:7px 0 0 0;font-family:Arial,Helvetica,sans-serif;
              font-size:13px;line-height:20px;color:#252B31;
              mso-line-height-rule:exactly;">
            {summary}
          </td>
        </tr>
        """

    return f"""
    <table role="presentation" width="100%" border="0" cellspacing="0"
        cellpadding="0" style="width:100%;border-collapse:collapse;">
      {category_html}
      <tr>
        <td style="padding:0;font-family:Arial,Helvetica,sans-serif;
            font-size:17px;line-height:22px;font-weight:bold;
            mso-line-height-rule:exactly;">
          <a href="{url}" style="color:#173C5E;text-decoration:underline;">
            {title}
          </a>
        </td>
      </tr>
      {summary_html}
      {win_html}
      <tr>
        <td style="padding:9px 0 0 0;font-family:Arial,Helvetica,sans-serif;
            font-size:10px;line-height:15px;color:#707B84;
            mso-line-height-rule:exactly;">
          {source}{f' &nbsp;•&nbsp; {date_label}' if date_label else ''}
          &nbsp;•&nbsp;
          <a href="{url}" style="color:#58738A;text-decoration:underline;">
            Read source
          </a>
        </td>
      </tr>
      {outlook_related_html(item)}
      {outlook_spacer(17)}
      <tr>
        <td height="1" bgcolor="#DFE5E9"
            style="height:1px;line-height:1px;font-size:0;
            background-color:#DFE5E9;">&nbsp;</td>
      </tr>
      {outlook_spacer(18)}
    </table>
    """


def outlook_section_html(title: str, items: list[dict]) -> str:
    if not items:
        return ""

    is_wins = title == "Trump Administration Wins"
    heading_color = "#8C241E" if is_wins else "#173C5E"
    rule_color = "#B42318" if is_wins else "#CBD6DE"
    stories = "".join(outlook_story_html(item, title) for item in items)

    return f"""
    <tr>
      <td style="padding:0 28px 0 28px;">
        <table role="presentation" width="100%" border="0" cellspacing="0"
            cellpadding="0" style="width:100%;border-collapse:collapse;">
          <tr>
            <td style="padding:0 0 8px 0;font-family:Arial,Helvetica,sans-serif;
                font-size:18px;line-height:23px;font-weight:bold;color:{heading_color};
                border-bottom:2px solid {rule_color};
                mso-line-height-rule:exactly;">
              {html.escape(title)}
            </td>
          </tr>
          {outlook_spacer(15)}
          <tr>
            <td style="padding:0;">{stories}</td>
          </tr>
        </table>
      </td>
    </tr>
    {outlook_spacer(8)}
    """


def build_outlook_html(briefing: dict, executive_only: bool = False) -> str:
    end = datetime.fromisoformat(briefing["window_end"]).astimezone(EASTERN)
    date_text = end.strftime("%A, %B %d, %Y").replace(" 0", " ")
    start_text = format_datetime(briefing.get("window_start", ""))
    end_text = format_datetime(briefing.get("window_end", ""))

    sections = briefing.get("sections", {})
    visible_sections = ["Trump Administration Wins", "Top Developments"]
    if not executive_only:
        visible_sections.extend(TOPIC_SECTIONS)

    win_count = len(sections.get("Trump Administration Wins", []))
    top_count = len(sections.get("Top Developments", []))
    total_count = sum(len(sections.get(section, [])) for section in visible_sections)

    section_markup = "".join(
        outlook_section_html(section, sections.get(section, []))
        for section in visible_sections
    )

    watch = briefing.get("what_to_watch", [])
    watch_markup = ""
    if watch:
        rows = ""
        for item in watch:
            rows += f"""
            <tr>
              <td width="16" valign="top" style="width:16px;padding:1px 0 7px 0;
                  font-family:Arial,Helvetica,sans-serif;font-size:13px;
                  line-height:19px;color:#48657D;">&#8226;</td>
              <td valign="top" style="padding:0 0 7px 0;
                  font-family:Arial,Helvetica,sans-serif;font-size:13px;
                  line-height:19px;color:#252B31;
                  mso-line-height-rule:exactly;">
                {html.escape(item)}
              </td>
            </tr>
            """

        watch_markup = f"""
        <tr>
          <td style="padding:0 28px;">
            <table role="presentation" width="100%" border="0" cellspacing="0"
                cellpadding="0" style="width:100%;border-collapse:collapse;">
              <tr>
                <td style="padding:0 0 8px 0;font-family:Arial,Helvetica,sans-serif;
                    font-size:18px;line-height:23px;font-weight:bold;color:#173C5E;
                    border-bottom:2px solid #CBD6DE;
                    mso-line-height-rule:exactly;">
                  What to Watch
                </td>
              </tr>
              {outlook_spacer(13)}
              <tr>
                <td>
                  <table role="presentation" width="100%" border="0" cellspacing="0"
                      cellpadding="0" style="width:100%;border-collapse:collapse;">
                    {rows}
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        {outlook_spacer(20)}
        """

    glance_parts = []
    if win_count:
        glance_parts.append(
            f"{win_count} Administration win{'s' if win_count != 1 else ''}"
        )
    if top_count:
        glance_parts.append(
            f"{top_count} top development{'s' if top_count != 1 else ''}"
        )
    glance_parts.append(f"{total_count} total item{'s' if total_count != 1 else ''}")
    glance = " &nbsp;•&nbsp; ".join(glance_parts)

    return f"""<!doctype html>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:v="urn:schemas-microsoft-com:vml"
      xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
  <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <!--[if mso]>
  <style type="text/css">
    body, table, td, a, p, span {{font-family: Arial, Helvetica, sans-serif !important;}}
    table {{border-collapse: collapse !important;}}
  </style>
  <![endif]-->
</head>
<body style="Margin:0;padding:0;background-color:#FFFFFF;">
  <table role="presentation" width="100%" border="0" cellspacing="0"
      cellpadding="0" bgcolor="#FFFFFF"
      style="width:100%;border-collapse:collapse;background-color:#FFFFFF;">
    <tr>
      <td align="center" style="padding:0;">
        <table role="presentation" width="720" border="0" cellspacing="0"
            cellpadding="0" align="center" bgcolor="#FFFFFF"
            style="width:720px;border-collapse:collapse;background-color:#FFFFFF;">
          <tr>
            <td bgcolor="#153A5A"
                style="padding:23px 28px 21px 28px;background-color:#153A5A;
                font-family:Arial,Helvetica,sans-serif;">
              <div style="font-size:28px;line-height:32px;font-weight:bold;
                  color:#FFFFFF;mso-line-height-rule:exactly;">
                Advanced Transportation News Update
              </div>
              <div style="padding-top:6px;font-size:13px;line-height:18px;
                  color:#DCE8F0;mso-line-height-rule:exactly;">
                {html.escape(date_text)}
              </div>
            </td>
          </tr>

          <tr>
            <td style="padding:11px 28px 0 28px;font-family:Arial,Helvetica,sans-serif;
                font-size:9px;line-height:14px;color:#687681;
                mso-line-height-rule:exactly;">
              <strong>24-HOUR COVERAGE WINDOW:</strong>
              {html.escape(start_text)} through {html.escape(end_text)}
            </td>
          </tr>

          <tr>
            <td style="padding:10px 28px 0 28px;">
              <table role="presentation" width="100%" border="0" cellspacing="0"
                  cellpadding="0" bgcolor="#F2F5F7"
                  style="width:100%;border-collapse:collapse;background-color:#F2F5F7;">
                <tr>
                  <td style="padding:8px 11px;font-family:Arial,Helvetica,sans-serif;
                      font-size:10px;line-height:15px;color:#5D6B78;
                      mso-line-height-rule:exactly;">
                    <strong>TODAY AT A GLANCE:</strong> {glance}
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          {outlook_spacer(17)}

          <tr>
            <td style="padding:0 28px 0 28px;">
              <table role="presentation" width="100%" border="0" cellspacing="0"
                  cellpadding="0" bgcolor="#EDF4F8"
                  style="width:100%;border-collapse:collapse;background-color:#EDF4F8;
                  border-left:4px solid #4D7898;">
                <tr>
                  <td style="padding:14px 16px 15px 16px;
                      font-family:Arial,Helvetica,sans-serif;">
                    <div style="font-size:10px;line-height:14px;font-weight:bold;
                        color:#244D6B;letter-spacing:.3px;
                        mso-line-height-rule:exactly;">
                      EXECUTIVE SUMMARY
                    </div>
                    <div style="padding-top:6px;font-size:14px;line-height:21px;
                        color:#24323D;mso-line-height-rule:exactly;">
                      {html.escape(briefing.get("executive_summary", ""))}
                    </div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          {outlook_spacer(25)}
          {section_markup}
          {watch_markup}

          <tr>
            <td style="padding:0 28px 24px 28px;">
              <table role="presentation" width="100%" border="0" cellspacing="0"
                  cellpadding="0" style="width:100%;border-collapse:collapse;
                  border-top:1px solid #DFE4E8;">
                <tr>
                  <td style="padding:10px 0 0 0;font-family:Arial,Helvetica,sans-serif;
                      font-size:9px;line-height:14px;color:#7B848C;
                      mso-line-height-rule:exactly;">
                    Public-source, AI-assisted news update. Review summaries, links,
                    executive-order citations, and Administration attributions before
                    distribution.
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""



def build_plain_text(briefing: dict, executive_only: bool = False) -> str:
    end = datetime.fromisoformat(briefing["window_end"]).astimezone(EASTERN)
    lines = [
        "ADVANCED TRANSPORTATION NEWS UPDATE",
        end.strftime("%A, %B %d, %Y").replace(" 0", " "),
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


def copy_controls(
    full_html: str,
    full_text: str,
    short_html: str,
    short_text: str,
    subject_line: str,
) -> None:
    component = f"""
    <!doctype html><html><head><meta charset="utf-8"><style>
    body{{margin:0;font-family:Arial,Helvetica,sans-serif;background:transparent}}
    .row{{display:flex;gap:9px;align-items:center;flex-wrap:wrap}}
    button{{border:1px solid #153a5a;border-radius:6px;padding:10px 14px;
      font-size:13px;font-weight:700;cursor:pointer}}
    #full{{background:#153a5a;color:white}}
    #short,#subject{{background:white;color:#153a5a}}
    #status{{font-size:12px;color:#5f6f7b;min-height:17px}}
    </style></head><body><div class="row">
      <button id="full" onclick="copyVersion('full')">Copy for Outlook</button>
      <button id="short" onclick="copyVersion('short')">Copy Executive Version</button>
      <button id="subject" onclick="copySubject()">Copy Subject Line</button>
      <span id="status"></span>
    </div><script>
    const fullHtml={json.dumps(full_html)};
    const fullText={json.dumps(full_text)};
    const shortHtml={json.dumps(short_html)};
    const shortText={json.dumps(short_text)};
    const subjectLine={json.dumps(subject_line)};
    function status(msg){{const el=document.getElementById('status');el.textContent=msg;
      setTimeout(()=>el.textContent='',2800)}}
    async function copyVersion(which){{
      const h=which==='full'?fullHtml:shortHtml;
      const t=which==='full'?fullText:shortText;
      try{{
        await navigator.clipboard.write([new ClipboardItem({{
          'text/html':new Blob([h],{{type:'text/html'}}),
          'text/plain':new Blob([t],{{type:'text/plain'}})
        }})]);
        status('Copied Outlook-formatted email.');
      }} catch(e){{
        try{{await navigator.clipboard.writeText(t);status('Copied as plain text.')}}
        catch(e2){{status('Browser blocked clipboard access.')}}
      }}
    }}
    async function copySubject(){{
      try{{await navigator.clipboard.writeText(subjectLine);status('Subject line copied.')}}
      catch(e){{status('Browser blocked clipboard access.')}}
    }}
    </script></body></html>
    """
    components.html(component, height=60)


MANUAL_IMPORT_USER_AGENT = (
    "TransportationNewsUpdate/4.0 manual-intake metadata fetcher"
)
URL_PATTERN = re.compile(r"(?:https?://|www\.)[^\s<>\"'`]+", re.IGNORECASE)



SOURCE_ONLY_LABELS = {
    "msn", "msn.com", "aol", "aol.com", "yahoo", "yahoo finance",
    "google news", "reuters", "associated press", "ap", "read more",
    "click here", "article", "link",
}


def normalize_import_url(value: str) -> str:
    value = html.unescape(value or "").strip()
    value = value.strip("<>[](){}\"'`")
    value = re.sub(r"[.,;:!?]+$", "", value)
    return value


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


def is_source_only(value: str, source: str = "") -> bool:
    cleaned = clean_spaces(re.sub(r"^[•\-–—\s]+", "", value or "")).strip(" :|")
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
    value = re.sub(r"https?://\S+", " ", value or "")
    value = clean_spaces(value)
    value = re.sub(r"^[•\-–—\d.)\s]+", "", value)
    value = value.strip("<>[](){}\"'` ")
    if source:
        value = re.sub(
            rf"\s*[-|–—]\s*{re.escape(source)}\s*$",
            "",
            value,
            flags=re.IGNORECASE,
        )
    return clean_spaces(value)


def context_for_link(lines: list[str], index: int, url: str) -> tuple[str, str]:
    same_line = clean_headline_candidate(lines[index].replace(url, ""))
    context_lines = []
    for offset in (0, -1, -2, 1):
        pos = index + offset
        if 0 <= pos < len(lines):
            candidate = clean_spaces(lines[pos])
            if candidate and candidate not in context_lines:
                context_lines.append(candidate)

    context = " ".join(context_lines)[:1200]
    if same_line and not is_source_only(same_line):
        return same_line, context

    for offset in (-1, -2, 1):
        pos = index + offset
        if 0 <= pos < len(lines):
            candidate = clean_headline_candidate(lines[pos])
            if candidate and not is_source_only(candidate):
                return candidate, context

    return "", context


def first_html_match(document: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, document, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return clean_spaces(html.unescape(re.sub(r"<[^>]+>", " ", match.group(1))))
    return ""


def fetch_link_metadata(record: dict) -> dict:
    enriched = dict(record)
    try:
        response = requests.get(
            record["url"],
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Safari/537.36"
                )
            },
            timeout=18,
            allow_redirects=True,
        )
        response.raise_for_status()
        document = response.text[:750000]
        final_url = normalize_import_url(response.url)
        source = source_from_url(final_url)

        fetched_title = first_html_match(
            document,
            [
                r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']',
                r'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']og:title["\']',
                r"<title[^>]*>(.*?)</title>",
            ],
        )
        description = first_html_match(
            document,
            [
                r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
                r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
                r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
            ],
        )

        pasted = clean_headline_candidate(enriched.get("pasted_headline", ""), source)
        fetched = clean_headline_candidate(fetched_title, source)
        if pasted and not is_source_only(pasted, source):
            title = pasted
        elif fetched and not is_source_only(fetched, source):
            title = fetched
        else:
            title = clean_headline_candidate(enriched.get("pasted_context", ""), source)

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
        pasted = clean_headline_candidate(record.get("pasted_headline", ""), source)
        context_title = clean_headline_candidate(record.get("pasted_context", ""), source)
        title = pasted if pasted and not is_source_only(pasted, source) else context_title
        enriched.update(
            {
                "source": source,
                "title": title[:260],
                "description": "",
                "fetch_status": f"Metadata unavailable: {clean_spaces(str(exc))[:160]}",
            }
        )
    return enriched


def extract_supplemental_items(raw_text: str, fetch_metadata: bool = True) -> list[dict]:
    lines = [line.rstrip() for line in (raw_text or "").splitlines()]
    pattern = re.compile(r"https?://[^\s<>]+")
    records = []
    seen = set()

    for index, line in enumerate(lines):
        # Strip malformed wrappers such as <https://...>.
        for match in pattern.finditer(html.unescape(line)):
            url = normalize_import_url(match.group(0))
            if not url or url in seen:
                continue
            seen.add(url)
            pasted_headline, context = context_for_link(lines, index, match.group(0))
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
                "fetch_status": "Not fetched",
            }
            records.append(fetch_link_metadata(record) if fetch_metadata else record)

    return records


def raw_feed_text(raw_feed: dict) -> str:
    lines = []
    for index, item in enumerate(raw_feed.get("articles", []), start=1):
        lines.extend([
            f"{index}. {item.get('title', 'Untitled')}",
            f"{item.get('source', '')} | {item.get('published', '')}",
            item.get("url", ""),
            "",
        ])
    return "\n".join(lines)


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
        line.strip()
        for line in st.session_state.get(prefix + "what_to_watch", "").splitlines()
        if line.strip()
    ][:3]
    for section, items in list(edited.get("sections", {}).items()):
        kept = []
        for item in items:
            item_id = item["id"]
            if not st.session_state.get(prefix + item_id + "_include", True):
                continue
            item["title"] = st.session_state.get(
                prefix + item_id + "_title", item["title"]
            ).strip()
            item["summary"] = st.session_state.get(
                prefix + item_id + "_summary", item["summary"]
            ).strip()
            item["win_explanation"] = st.session_state.get(
                prefix + item_id + "_win", item.get("win_explanation", "")
            ).strip()
            kept.append(item)
        edited["sections"][section] = kept
    return edited


def render_editor(briefing: dict, edition_key: str) -> None:
    prefix = f"edit_{edition_key}_"
    st.text_area("Executive Summary", key=prefix + "executive_summary", height=120)
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
                st.text_area("Summary", key=prefix + item_id + "_summary", height=90)
                if item.get("is_administration_win"):
                    st.text_area(
                        "Why this is a Trump Administration win",
                        key=prefix + item_id + "_win",
                        height=95,
                    )
                st.caption(
                    f"{item.get('source', '')} · {item.get('date_label', '')}"
                )


st.markdown("""
<style>
.block-container{max-width:940px;padding-top:1.4rem;padding-bottom:4rem}
[data-testid="stHeader"]{background:rgba(255,255,255,.94)}
</style>
""", unsafe_allow_html=True)

render_owner_access()

if not LATEST_RAW_PATH.exists():
    st.error(
        "No raw 24-hour news feed exists yet. Run the GitHub Action "
        "'Daily Transportation Raw News Collection' once."
    )
    st.stop()

raw_feed = load_json(LATEST_RAW_PATH)
if not raw_feed.get("window_end"):
    st.warning(
        "The new raw-feed workflow has not run yet. In GitHub, open Actions and "
        "manually run **Daily Transportation Raw News Collection** once. Then "
        "refresh this page."
    )
    st.stop()

end = datetime.fromisoformat(raw_feed["window_end"]).astimezone(EASTERN)
build_key = end.date().isoformat()

st.title("Advanced Transportation News Update")
st.caption(
    f"Automated raw feed covers the preceding 24 hours through "
    f"{end.strftime('%-I:%M %p ET on %B %d, %Y').replace(' 0', ' ')}"
)

build_tab, preview_tab, edit_tab, raw_tab, status_tab = st.tabs(
    [
        "Build Today’s Update",
        "Email Preview",
        "Review & Edit",
        "Raw 24-Hour Feed",
        "Status",
    ]
)

with build_tab:
    st.subheader("1. Automated 24-hour feed")
    st.write(
        f"GitHub collected **{raw_feed.get('candidate_count', 0)}** raw candidate "
        "articles without using OpenAI."
    )
    with st.expander("View the automated feed as text"):
        st.text_area(
            "Automated feed",
            value=raw_feed_text(raw_feed),
            height=320,
            disabled=True,
            label_visibility="collapsed",
        )

    st.subheader("2. Paste the supplemental daily email")
    paste_key = f"supplemental_paste_{build_key}"
    st.text_area(
        "Supplemental email",
        key=paste_key,
        height=300,
        placeholder=(
            "Paste the complete email here, including headlines, notes, and links. "
            "Links enclosed in < > or followed by punctuation will be cleaned."
        ),
        label_visibility="collapsed",
    )
    fetch_metadata = st.checkbox(
        "Read public page titles and descriptions before the AI pass",
        value=True,
        help=(
            "Recommended. If a page is blocked or paywalled, the pasted headline "
            "and surrounding text are still preserved."
        ),
    )

    left, right = st.columns([0.72, 0.28])
    with left:
        if st.button(
            "Preview and clean supplemental items",
            use_container_width=True,
        ):
            with st.spinner("Cleaning links and reading available page metadata…"):
                records = extract_supplemental_items(
                    st.session_state.get(paste_key, ""),
                    fetch_metadata=fetch_metadata,
                )
            st.session_state[f"supplemental_records_{build_key}"] = records
            if records:
                st.success(
                    f"Found {len(records)} unique supplemental link"
                    f"{'s' if len(records) != 1 else ''}."
                )
            else:
                st.warning("No HTTP or HTTPS links were found.")
    with right:
        if st.button("Clear", use_container_width=True):
            st.session_state.pop(f"supplemental_records_{build_key}", None)
            st.session_state.pop(f"generated_briefing_{build_key}", None)
            st.rerun()

    records = st.session_state.get(f"supplemental_records_{build_key}", [])
    if records:
        st.markdown("### Supplemental items that will all be included")
        st.caption(
            "Edit any weak headline before the AI pass. Every listed URL must appear "
            "as a story or as true same-event additional coverage."
        )
        for index, record in enumerate(records):
            with st.container(border=True):
                title_key = f"supp_title_{build_key}_{record['id']}"
                source_key = f"supp_source_{build_key}_{record['id']}"
                if title_key not in st.session_state:
                    st.session_state[title_key] = record.get("title", "")
                if source_key not in st.session_state:
                    st.session_state[source_key] = record.get("source", "")
                st.text_input(
                    f"Headline {index + 1}",
                    key=title_key,
                )
                st.text_input(
                    "Source",
                    key=source_key,
                )
                st.markdown(f"[Open link]({record['url']})")
                if record.get("pasted_context"):
                    st.caption(record["pasted_context"][:500])
                st.caption(record.get("fetch_status", ""))

        if owner_authenticated():
            if st.button(
                "Build Today’s Advanced Transportation News Update with AI",
                type="primary",
                use_container_width=True,
            ):
                api_key = secret_value("openai_api_key")
                model = secret_value("openai_model", DEFAULT_OPENAI_MODEL)
                if not api_key:
                    st.error("Add openai_api_key to Streamlit Secrets.")
                else:
                    edited_records = []
                    for record in records:
                        edited = dict(record)
                        edited["title"] = st.session_state.get(
                            f"supp_title_{build_key}_{record['id']}",
                            record.get("title", ""),
                        ).strip()
                        edited["pasted_headline"] = edited["title"]
                        edited["source"] = st.session_state.get(
                            f"supp_source_{build_key}_{record['id']}",
                            record.get("source", ""),
                        ).strip()
                        edited_records.append(edited)

                    with st.spinner(
                        "Running one AI editorial pass across the automated feed "
                        "and all supplemental items…"
                    ):
                        try:
                            briefing = generate_briefing_from_records(
                                raw_feed,
                                edited_records,
                                api_key,
                                model,
                            )
                            st.session_state[
                                f"generated_briefing_{build_key}"
                            ] = briefing
                            # Reset editor state for the new generation.
                            for key in list(st.session_state):
                                if key.startswith(f"edit_{build_key}_"):
                                    del st.session_state[key]
                            st.success("Today’s Advanced Transportation News Update is ready.")
                            st.rerun()
                        except Exception as exc:
                            st.error(str(exc))
        else:
            st.info(
                "Unlock Owner controls in the sidebar to run the AI editorial pass."
            )
    else:
        st.info(
            "The supplemental email is optional. To build using only the automated "
            "24-hour feed, paste nothing and click the button below."
        )
        if owner_authenticated() and st.button(
            "Build from Automated Feed Only",
            type="primary",
            use_container_width=True,
        ):
            api_key = secret_value("openai_api_key")
            model = secret_value("openai_model", DEFAULT_OPENAI_MODEL)
            if not api_key:
                st.error("Add openai_api_key to Streamlit Secrets.")
            else:
                with st.spinner("Building today’s update…"):
                    try:
                        briefing = generate_briefing_from_records(
                            raw_feed, [], api_key, model
                        )
                        st.session_state[
                            f"generated_briefing_{build_key}"
                        ] = briefing
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

briefing = st.session_state.get(f"generated_briefing_{build_key}")

with preview_tab:
    if not briefing:
        st.info(
            "Build today’s update first. Open **Build Today’s Update**, paste the "
            "supplemental email, preview the links, and run the AI editorial pass."
        )
    else:
        initialize_editor(briefing, build_key)
        current = (
            edited_briefing(briefing, build_key)
            if owner_authenticated()
            else briefing
        )
        web_preview_html = build_web_preview_html(current, executive_only=False)
        outlook_full_html = build_outlook_html(current, executive_only=False)
        outlook_short_html = build_outlook_html(current, executive_only=True)
        full_text = build_plain_text(current, executive_only=False)
        short_text = build_plain_text(current, executive_only=True)
        subject_line = (
            f"Advanced Transportation News Update — "
            f"{end.strftime('%B %d, %Y').replace(' 0', ' ')}"
        )

        copy_controls(
            outlook_full_html,
            full_text,
            outlook_short_html,
            short_text,
            subject_line,
        )
        st.download_button(
            "Download Outlook HTML",
            data=outlook_full_html,
            file_name=f"news-update-{build_key}.html",
            mime="text/html",
        )
        st.download_button(
            "Download briefing JSON backup",
            data=json.dumps(current, indent=2, ensure_ascii=False),
            file_name=f"news-update-{build_key}.json",
            mime="application/json",
        )
        st.divider()
        st.html(web_preview_html)
        with st.expander("Preview the Outlook-optimized layout"):
            st.html(outlook_full_html)

with edit_tab:
    if not briefing:
        st.info("Build today’s update first.")
    elif owner_authenticated():
        initialize_editor(briefing, build_key)
        st.caption(
            "Edits affect this browser session and the copied email."
        )
        render_editor(briefing, build_key)
    else:
        st.info("Unlock Owner controls to edit the generated briefing.")

with raw_tab:
    st.write(
        f"**{raw_feed.get('candidate_count', 0)} raw candidates** collected "
        "without AI during the preceding 24 hours."
    )
    for section, count in sorted(
        raw_feed.get("candidate_counts", {}).items()
    ):
        st.write(f"- {section}: {count}")
    st.text_area(
        "Raw feed",
        value=raw_feed_text(raw_feed),
        height=520,
        disabled=True,
        label_visibility="collapsed",
    )

with status_tab:
    st.write(f"**Raw feed generated:** {format_datetime(raw_feed.get('generated_at', ''))}")
    st.write(
        f"**Coverage:** {format_datetime(raw_feed['window_start'])} through "
        f"{format_datetime(raw_feed['window_end'])}"
    )
    st.write(
        f"**Automated candidates:** {raw_feed.get('candidate_count', 0)}"
    )
    if briefing:
        st.write(
            f"**Obvious unrelated automated records filtered before AI:** "
            f"{briefing.get('automated_filtered_out_count', 0)}"
        )
        st.write(f"**AI model:** {briefing.get('model', '')}")
        usage = briefing.get("usage", {})
        st.write(
            f"**AI tokens:** {int(usage.get('input_tokens', 0)):,} input; "
            f"{int(usage.get('output_tokens', 0)):,} output"
        )
        if briefing.get("estimated_cost") is not None:
            st.write(
                f"**Estimated OpenAI cost:** ${briefing['estimated_cost']:.4f}"
            )
        st.write(
            f"**Supplemental links:** {briefing.get('supplemental_count', 0)}"
        )
        st.write(
            f"**Supplemental links accounted for:** "
            f"{briefing.get('supplemental_accounted_count', 0)}"
        )
    else:
        st.info("No OpenAI call has been made in this browser session yet.")

    if raw_feed.get("source_errors"):
        st.warning("Some source requests failed:")
        for error in raw_feed["source_errors"]:
            st.code(error)
    else:
        st.success("All configured raw-news source requests completed.")
