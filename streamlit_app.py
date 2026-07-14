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
    EO_REFERENCE,
    OPENAI_RESPONSES_ENDPOINT,
    OPENAI_TOKEN_PRICES,
    SECTION_ORDER,
    SOURCE_PREFERENCE,
    TOPIC_SECTIONS,
    TRANSIENT_OPENAI_STATUS_CODES,
    arrange_sections,
    clean_spaces,
    generate_daily_briefing,
    stable_id,
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
    is_top = title == "Top Developments"
    heading_color = "#8c241e" if is_wins else "#173c5e"

    if is_wins or is_top:
        content = "".join(article_html(item, title) for item in items)
    else:
        featured = items[:4]
        additional = items[4:]
        content = "".join(article_html(item, title) for item in featured)
        if additional:
            compact = "".join(compact_article_html(item) for item in additional)
            content += f"""
            <div style="font-size:12px;font-weight:800;color:#48657d;
                text-transform:uppercase;letter-spacing:.35px;margin:2px 0 9px 0;">
              Additional Headlines
            </div>
            {compact}
            """

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
    is_top = title == "Top Developments"
    heading_color = "#8C241E" if is_wins else "#173C5E"
    rule_color = "#B42318" if is_wins else "#CBD6DE"

    if is_wins or is_top:
        stories = "".join(outlook_story_html(item, title) for item in items)
    else:
        featured = items[:4]
        additional = items[4:]
        stories = "".join(outlook_story_html(item, title) for item in featured)
        if additional:
            stories += f"""
            <table role="presentation" width="100%" border="0" cellspacing="0"
                cellpadding="0" style="width:100%;border-collapse:collapse;">
              <tr>
                <td style="padding:0 0 10px 0;font-family:Arial,Helvetica,sans-serif;
                    font-size:10px;line-height:14px;font-weight:bold;color:#48657D;
                    text-transform:uppercase;letter-spacing:.4px;
                    mso-line-height-rule:exactly;">
                  Additional Headlines
                </td>
              </tr>
              <tr>
                <td style="padding:0;">
                  {"".join(outlook_story_html(item, title, compact=True)
                           for item in additional)}
                </td>
              </tr>
            </table>
            """

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
                News Update
              </div>
              <div style="padding-top:6px;font-size:13px;line-height:18px;
                  color:#DCE8F0;mso-line-height-rule:exactly;">
                {html.escape(date_text)}
              </div>
              <div style="font-size:12px;line-height:17px;color:#DCE8F0;
                  mso-line-height-rule:exactly;">
                UAS, C-UAS, and Advanced Transportation
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


def normalize_import_url(value: str) -> str:
    value = html.unescape(value or "").replace("\u200b", "").strip()
    value = value.lstrip("<([{'")
    value = value.rstrip(">)]}'\".,;:!?\\")
    if value.lower().startswith("www."):
        value = "https://" + value
    return value


def url_is_public(value: str) -> bool:
    try:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        host = parsed.hostname.casefold()
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            return False
        addresses = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                return False
        return True
    except (ValueError, OSError, socket.gaierror):
        return False


def context_for_url(lines: list[str], index: int) -> str:
    nearby = []
    for line_index in range(max(0, index - 2), min(len(lines), index + 2)):
        text = clean_spaces(lines[line_index])
        if text:
            nearby.append(text)
    return clean_spaces(" ".join(nearby))[:900]


def extract_pasted_links(raw_text: str) -> list[dict]:
    text = html.unescape(raw_text or "").replace("\u200b", "")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    records = []
    seen = set()

    for line_index, line in enumerate(lines):
        for match in URL_PATTERN.finditer(line):
            url = normalize_import_url(match.group(0))
            if not url.startswith(("https://", "http://")):
                continue
            key = url.casefold()
            if key in seen:
                continue
            seen.add(key)
            records.append(
                {
                    "id": stable_id("manual", url),
                    "url": url,
                    "pasted_context": context_for_url(lines, line_index),
                    "title": "",
                    "description": "",
                    "page_text": "",
                    "source": urlparse(url).netloc.removeprefix("www."),
                    "final_url": url,
                    "fetch_status": "Not fetched",
                }
            )

    return records


def first_html_match(document: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, document, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return clean_spaces(html.unescape(re.sub(r"<[^>]+>", " ", match.group(1))))
    return ""


def fetch_link_metadata(record: dict) -> dict:
    enriched = dict(record)
    url = record["url"]
    if not url_is_public(url):
        enriched["fetch_status"] = "Skipped unsafe or invalid address"
        return enriched

    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": MANUAL_IMPORT_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=14,
            allow_redirects=True,
            stream=True,
        )
        response.raise_for_status()
        final_url = response.url
        if not url_is_public(final_url):
            enriched["fetch_status"] = "Redirected to an unsafe address"
            return enriched

        content_type = response.headers.get("Content-Type", "").lower()
        if "html" not in content_type:
            enriched["final_url"] = final_url
            enriched["source"] = urlparse(final_url).netloc.removeprefix("www.")
            enriched["fetch_status"] = f"Non-HTML source ({content_type or 'unknown type'})"
            return enriched

        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=16384, decode_unicode=False):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total >= 750_000:
                break
        raw = b"".join(chunks)
        encoding = response.encoding or "utf-8"
        document = raw.decode(encoding, errors="replace")

        title = first_html_match(
            document,
            [
                r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']',
                r'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']og:title["\']',
                r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\'](.*?)["\']',
                r"<title[^>]*>(.*?)</title>",
            ],
        )
        description = first_html_match(
            document,
            [
                r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
                r'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']og:description["\']',
                r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
            ],
        )

        body = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>", " ", document)
        body = clean_spaces(html.unescape(re.sub(r"<[^>]+>", " ", body)))

        enriched.update(
            {
                "title": title[:300],
                "description": description[:1200],
                "page_text": body[:3500],
                "final_url": final_url,
                "source": urlparse(final_url).netloc.removeprefix("www."),
                "fetch_status": "Metadata fetched",
            }
        )
    except requests.RequestException as exc:
        enriched["fetch_status"] = f"Could not fetch page: {type(exc).__name__}"

    return enriched


def flatten_briefing_stories(briefing: dict) -> list[dict]:
    stories = []
    seen = set()
    for section_name in SECTION_ORDER:
        for item in briefing.get("sections", {}).get(section_name, []):
            item_id = item.get("id", "")
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            copied = json.loads(json.dumps(item))
            if copied.get("section") not in TOPIC_SECTIONS:
                copied["section"] = (
                    section_name if section_name in TOPIC_SECTIONS else "Other Advanced Transportation"
                )
            stories.append(copied)
    return stories


def manual_import_schema() -> dict:
    group = {
        "type": "object",
        "properties": {
            "manual_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "action": {"type": "string", "enum": ["new_story", "merge_existing"]},
            "existing_story_id": {"type": "string"},
            "section": {"type": "string", "enum": TOPIC_SECTIONS},
            "importance": {"type": "integer", "minimum": 1, "maximum": 10},
            "canonical_title": {"type": "string"},
            "summary": {"type": "string"},
            "is_administration_win": {"type": "boolean"},
            "eo_number": {
                "type": "string",
                "enum": ["", "EO 14307", "EO 14305", "EO 14304"],
            },
            "eo_section": {"type": "string"},
            "win_explanation": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": [
            "manual_ids", "action", "existing_story_id", "section", "importance",
            "canonical_title", "summary", "is_administration_win", "eo_number",
            "eo_section", "win_explanation", "confidence",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "revised_executive_summary": {"type": "string"},
            "revised_what_to_watch": {"type": "array", "items": {"type": "string"}},
            "groups": {"type": "array", "items": group},
        },
        "required": ["revised_executive_summary", "revised_what_to_watch", "groups"],
        "additionalProperties": False,
    }


def extract_openai_text(data: dict) -> str:
    parts = []
    refusals = []
    for output in data.get("output", []):
        if output.get("type") != "message":
            continue
        for content in output.get("content", []):
            if content.get("type") == "output_text":
                parts.append(content.get("text", ""))
            elif content.get("type") == "refusal":
                refusals.append(content.get("refusal", "Request refused."))
    if refusals:
        raise RuntimeError("OpenAI declined the import: " + " ".join(refusals))
    text = "".join(parts).strip()
    if not text:
        raise RuntimeError("OpenAI returned no usable import analysis.")
    return text


def manual_import_cost(model: str, usage: dict) -> float | None:
    prices = OPENAI_TOKEN_PRICES.get(model)
    if not prices:
        return None
    return (
        int(usage.get("input_tokens", 0) or 0) * prices["input"] / 1_000_000
        + int(usage.get("output_tokens", 0) or 0) * prices["output"] / 1_000_000
    )


def analyze_manual_links(
    briefing: dict,
    records: list[dict],
    api_key: str,
    model: str,
) -> tuple[dict, dict, float | None]:
    existing = flatten_briefing_stories(briefing)
    compact_existing = [
        {
            "id": item.get("id", ""),
            "title": item.get("title", ""),
            "summary": item.get("summary", ""),
            "section": item.get("section", ""),
            "is_administration_win": bool(item.get("is_administration_win")),
            "eo_number": item.get("eo_number", ""),
        }
        for item in existing
    ]
    compact_manual = [
        {
            "id": item["id"],
            "url": item.get("final_url") or item["url"],
            "source": item.get("source", ""),
            "pasted_context": item.get("pasted_context", ""),
            "page_title": item.get("title", ""),
            "page_description": item.get("description", ""),
            "page_text_excerpt": item.get("page_text", ""),
        }
        for item in records
    ]

    developer = f"""
You are integrating a second, manually pasted daily news email into an existing U.S.
advanced-transportation briefing. Account for EVERY manual_id exactly once. No pasted link
may be silently omitted. A manual link must either become a new story or merge into a clearly
matching existing story as additional coverage.

RULES
- Group manual links only when they cover the same concrete event. Broad topic similarity is
  not enough.
- Use merge_existing only for a true same-event match, and provide its existing_story_id.
- Otherwise create a new story and assign the closest listed section. If ambiguous, use
  Other Advanced Transportation rather than dropping the item.
- Write specific, factual titles and 1-2 sentence summaries using only supplied material.
- Check every group for a direct, supportable Trump Administration win. Positive private
  news alone is not a win. Use EO fields only when the supplied information supports them.
- Win language may be confident and pro-American but must remain factual and tied to a
  concrete Administration action or result.
- Revise the Executive Summary to reflect the combined briefing in 2-3 sentences, 55-95
  words. Return 0-3 grounded What to Watch items.
- Prefer primary and authoritative sources: {SOURCE_PREFERENCE}

{EO_REFERENCE}
""".strip()

    user = json.dumps(
        {
            "existing_briefing_stories": compact_existing,
            "manual_records": compact_manual,
        },
        ensure_ascii=False,
    )

    payload = {
        "model": model,
        "input": [
            {"role": "developer", "content": developer},
            {"role": "user", "content": user},
        ],
        "reasoning": {"effort": "none"},
        "max_output_tokens": 12000,
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "manual_news_import",
                "strict": True,
                "schema": manual_import_schema(),
            }
        },
    }

    errors = []
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
            result = json.loads(extract_openai_text(data))
            usage = data.get("usage") or {}
            return result, usage, manual_import_cost(model, usage)

        detail = clean_spaces(response.text)[:700]
        errors.append(f"HTTP {response.status_code}: {detail}")
        if response.status_code in TRANSIENT_OPENAI_STATUS_CODES and attempt < 3:
            time.sleep((2 ** attempt) + random.uniform(0.2, 1.0))
            continue
        raise RuntimeError(f"OpenAI import returned HTTP {response.status_code}: {detail}")

    raise RuntimeError("OpenAI import failed after retries: " + " | ".join(errors[-4:]))


def fallback_section(record: dict) -> str:
    text = " ".join(
        [
            record.get("title", ""),
            record.get("description", ""),
            record.get("pasted_context", ""),
            record.get("url", ""),
        ]
    ).casefold()
    if any(term in text for term in ["counter-uas", "c-uas", "counter drone", "drone detection", "drone threat"]):
        return "UAS Security and C-UAS"
    if any(term in text for term in ["evtol", "advanced air mobility", "air taxi", "powered-lift", "vertiport", "eipp"]):
        return "eVTOL Integration Pilot Program and AAM"
    if any(term in text for term in ["autonomous vehicle", "robotaxi", "self-driving", "driverless", "automated driving"]):
        return "Autonomous Vehicles"
    if any(term in text for term in ["drone", "uas", "unmanned aircraft", "bvlos", "remote id"]):
        return "UAS and Drones"
    if any(term in text for term in ["federal register", "faa.gov", "dot.gov", "nhtsa.gov", "fra.gov", "whitehouse.gov"]):
        return "Federal Actions"
    return "Other Advanced Transportation"


def merge_manual_analysis(
    briefing: dict,
    records: list[dict],
    analysis: dict,
    usage: dict,
    cost: float | None,
) -> tuple[dict, dict]:
    merged = json.loads(json.dumps(briefing))
    stories = flatten_briefing_stories(merged)
    existing_lookup = {item["id"]: item for item in stories}
    record_lookup = {item["id"]: item for item in records}
    used_manual = set()
    new_story_ids = []
    merged_link_count = 0

    for group in analysis.get("groups", []):
        manual_ids = []
        for manual_id in group.get("manual_ids", []):
            if manual_id in record_lookup and manual_id not in used_manual:
                used_manual.add(manual_id)
                manual_ids.append(manual_id)
        if not manual_ids:
            continue

        group_records = [record_lookup[item_id] for item_id in manual_ids]
        existing_id = group.get("existing_story_id", "")
        action = group.get("action", "new_story")
        section = group.get("section", "")
        if section not in TOPIC_SECTIONS:
            section = fallback_section(group_records[0])

        if action == "merge_existing" and existing_id in existing_lookup:
            story = existing_lookup[existing_id]
            related = story.setdefault("also_covered", [])
            known_urls = {item.get("url", "") for item in related}
            known_urls.add(story.get("url", ""))
            for record in group_records:
                record_url = record.get("final_url") or record["url"]
                if record_url in known_urls:
                    continue
                known_urls.add(record_url)
                related.append({"source": record.get("source", "Imported source"), "url": record_url})
                merged_link_count += 1
            if clean_spaces(group.get("canonical_title", "")):
                story["title"] = clean_spaces(group["canonical_title"])
            if clean_spaces(group.get("summary", "")):
                story["summary"] = clean_spaces(group["summary"])
            story["importance"] = max(
                int(story.get("importance", 1)),
                max(1, min(10, int(group.get("importance", 1) or 1))),
            )
            if group.get("is_administration_win"):
                story["is_administration_win"] = True
                story["eo_number"] = group.get("eo_number", "")
                story["eo_name"] = EO_DISPLAY_NAMES.get(story["eo_number"], "")
                story["eo_section"] = clean_spaces(group.get("eo_section", ""))
                story["win_explanation"] = clean_spaces(group.get("win_explanation", ""))
            continue

        primary = group_records[0]
        primary_url = primary.get("final_url") or primary["url"]
        title = clean_spaces(group.get("canonical_title", "")) or primary.get("title") or primary.get("pasted_context") or primary_url
        summary = clean_spaces(group.get("summary", "")) or primary.get("description") or "Imported from the supplemental daily news email."
        story_id = stable_id("manual-story", *manual_ids)
        related = [
            {
                "source": record.get("source", "Imported source"),
                "url": record.get("final_url") or record["url"],
            }
            for record in group_records[1:]
        ]
        story = {
            "id": story_id,
            "title": title,
            "summary": summary,
            "source": primary.get("source", "Imported source"),
            "url": primary_url,
            "published": datetime.now(EASTERN).isoformat(),
            "date_label": datetime.now(EASTERN).strftime("%b. %d, %Y").replace(" 0", " "),
            "section": section,
            "importance": max(1, min(10, int(group.get("importance", 4) or 4))),
            "confidence": group.get("confidence", "medium"),
            "is_administration_win": bool(group.get("is_administration_win", False)),
            "eo_number": group.get("eo_number", ""),
            "eo_name": EO_DISPLAY_NAMES.get(group.get("eo_number", ""), ""),
            "eo_section": clean_spaces(group.get("eo_section", "")),
            "win_explanation": clean_spaces(group.get("win_explanation", "")),
            "also_covered": related,
            "manual_imported": True,
        }
        stories.append(story)
        existing_lookup[story_id] = story
        new_story_ids.append(story_id)

    missing_ids = [manual_id for manual_id in record_lookup if manual_id not in used_manual]
    for manual_id in missing_ids:
        record = record_lookup[manual_id]
        story_id = stable_id("manual-fallback", manual_id)
        story = {
            "id": story_id,
            "title": record.get("title") or record.get("pasted_context") or record["url"],
            "summary": record.get("description") or "Imported from the supplemental daily news email.",
            "source": record.get("source", "Imported source"),
            "url": record.get("final_url") or record["url"],
            "published": datetime.now(EASTERN).isoformat(),
            "date_label": datetime.now(EASTERN).strftime("%b. %d, %Y").replace(" 0", " "),
            "section": fallback_section(record),
            "importance": 3,
            "confidence": "low",
            "is_administration_win": False,
            "eo_number": "",
            "eo_name": "",
            "eo_section": "",
            "win_explanation": "",
            "also_covered": [],
            "manual_imported": True,
        }
        stories.append(story)
        new_story_ids.append(story_id)

    arranged = arrange_sections(stories)
    displayed_ids = {
        item.get("id")
        for items in arranged.values()
        for item in items
    }
    for story in stories:
        if story.get("id") in new_story_ids and story.get("id") not in displayed_ids:
            arranged[story["section"]].append(story)

    revised_summary = clean_spaces(analysis.get("revised_executive_summary", ""))
    if revised_summary:
        merged["executive_summary"] = revised_summary
    revised_watch = [
        clean_spaces(item)
        for item in analysis.get("revised_what_to_watch", [])[:3]
        if clean_spaces(item)
    ]
    if revised_watch:
        merged["what_to_watch"] = revised_watch
    merged["sections"] = arranged
    merged["manual_import"] = {
        "imported_at": datetime.now(EASTERN).isoformat(),
        "pasted_link_count": len(records),
        "new_story_count": len(new_story_ids),
        "merged_coverage_count": merged_link_count,
        "fallback_count": len(missing_ids),
        "usage": usage,
        "estimated_cost": cost,
    }
    report = merged["manual_import"]
    return merged, report


def render_manual_intake(current: dict, base_edition_key: str) -> None:
    st.subheader("Add links from another daily email")
    st.caption(
        "Paste the email text below. Every unique link will be accounted for: it will "
        "either become a story or be added as supplemental coverage to an existing story."
    )
    paste_key = f"manual_paste_{base_edition_key}"
    st.text_area(
        "Paste email text or links",
        key=paste_key,
        height=260,
        placeholder=(
            "Paste the full email here. Links enclosed in < > and links followed by "
            "commas, brackets, or other punctuation will be cleaned automatically."
        ),
    )
    fetch_pages = st.checkbox(
        "Fetch public page titles and descriptions before AI analysis",
        value=True,
        help="If a page is blocked or paywalled, the app still includes the link using the pasted context.",
    )

    extract_col, clear_col = st.columns([0.72, 0.28])
    with extract_col:
        if st.button("Extract and review links", use_container_width=True):
            records = extract_pasted_links(st.session_state.get(paste_key, ""))
            if fetch_pages and records:
                progress = st.progress(0, text="Reading public page metadata…")
                enriched = []
                for index, record in enumerate(records, start=1):
                    enriched.append(fetch_link_metadata(record))
                    progress.progress(index / len(records), text=f"Reading link {index} of {len(records)}…")
                progress.empty()
                records = enriched
            st.session_state[f"manual_records_{base_edition_key}"] = records
            if not records:
                st.warning("No HTTP or HTTPS links were found in the pasted text.")
            else:
                st.success(f"Extracted {len(records)} unique link{'s' if len(records) != 1 else ''}.")
    with clear_col:
        if st.button("Clear import", use_container_width=True):
            st.session_state.pop(f"manual_records_{base_edition_key}", None)
            st.session_state.pop(f"manual_override_{base_edition_key}", None)
            st.session_state.pop(f"manual_report_{base_edition_key}", None)
            st.rerun()

    records = st.session_state.get(f"manual_records_{base_edition_key}", [])
    if records:
        st.markdown(f"**Links that will be included: {len(records)}**")
        for index, record in enumerate(records, start=1):
            with st.expander(
                f"{index}. {record.get('title') or record.get('source') or record['url']}",
                expanded=False,
            ):
                st.markdown(f"[{record['url']}]({record['url']})")
                st.write(record.get("pasted_context") or "No surrounding pasted context.")
                st.caption(record.get("fetch_status", ""))

        if st.button("Integrate all links with AI", type="primary", use_container_width=True):
            api_key = secret_value("openai_api_key")
            model = secret_value("openai_model", DEFAULT_OPENAI_MODEL)
            if not api_key:
                st.error("Add openai_api_key to Streamlit Secrets.")
            else:
                with st.spinner("Classifying, deduplicating, checking Administration wins, and updating the briefing…"):
                    try:
                        analysis, usage, cost = analyze_manual_links(
                            current, records, api_key, model
                        )
                        merged, report = merge_manual_analysis(
                            current, records, analysis, usage, cost
                        )
                        st.session_state[f"manual_override_{base_edition_key}"] = merged
                        st.session_state[f"manual_report_{base_edition_key}"] = report
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

    report = st.session_state.get(f"manual_report_{base_edition_key}")
    if report:
        st.success(
            f"Integrated {report['pasted_link_count']} pasted links: "
            f"{report['new_story_count']} new stories and "
            f"{report['merged_coverage_count']} supplemental coverage links."
        )
        if report.get("fallback_count"):
            st.info(
                f"{report['fallback_count']} link(s) were included with fallback metadata "
                "because the AI did not account for them explicitly."
            )
        if report.get("estimated_cost") is not None:
            st.caption(f"Estimated OpenAI cost for this import: ${report['estimated_cost']:.4f}")


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

base_edition_key = edition_key
manual_override = st.session_state.get(f"manual_override_{base_edition_key}")
if manual_override:
    briefing = manual_override
    edition_key = base_edition_key + "_manual"

initialize_editor(briefing, edition_key)
current = edited_briefing(briefing, edition_key) if owner_authenticated() else briefing

end = datetime.fromisoformat(current["window_end"]).astimezone(EASTERN)
st.title("News Update")
st.caption(
    f"24-hour coverage through {end.strftime('%-I:%M %p ET on %B %d, %Y').replace(' 0', ' ')}"
)

preview_tab, intake_tab, edit_tab, status_tab = st.tabs(["Email Preview", "Add Daily Links", "Review & Edit", "Status"])

with preview_tab:
    web_preview_html = build_web_preview_html(current, executive_only=False)
    outlook_full_html = build_outlook_html(current, executive_only=False)
    outlook_short_html = build_outlook_html(current, executive_only=True)
    full_text = build_plain_text(current, executive_only=False)
    short_text = build_plain_text(current, executive_only=True)
    subject_line = f"Advanced Transportation News Update — {end.strftime('%B %d, %Y').replace(' 0', ' ')}"

    st.caption(
        "Copy for Outlook uses a separate Microsoft Outlook–optimized table layout "
        "with fixed spacing and inline formatting."
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
        file_name=f"news-update-{end.date().isoformat()}.html",
        mime="text/html",
    )
    st.divider()
    st.html(web_preview_html)

    with st.expander("Preview the Outlook-optimized layout"):
        st.html(outlook_full_html)

with intake_tab:
    if owner_authenticated():
        render_manual_intake(current, base_edition_key)
    else:
        st.info("Unlock Owner controls in the sidebar to paste and integrate supplemental links.")


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
    manual_info = current.get("manual_import", {})
    if manual_info:
        st.write(
            f"**Manual intake:** {manual_info.get('pasted_link_count', 0)} links; "
            f"{manual_info.get('new_story_count', 0)} new stories; "
            f"{manual_info.get('merged_coverage_count', 0)} supplemental links"
        )
        manual_usage = manual_info.get("usage", {})
        if manual_usage:
            st.write(
                f"**Manual-intake tokens:** {int(manual_usage.get('input_tokens', 0)):,} input; "
                f"{int(manual_usage.get('output_tokens', 0)):,} output"
            )
        if manual_info.get("estimated_cost") is not None:
            st.write(f"**Manual-intake estimated cost:** ${manual_info['estimated_cost']:.4f}")

    candidate_counts = current.get("candidate_counts", {})
    included_counts = current.get("included_counts", {})
    if candidate_counts:
        st.markdown("**Candidates collected by search family:**")
        for name, count in sorted(candidate_counts.items()):
            st.write(f"- {name}: {count}")
    if included_counts:
        st.markdown("**Items displayed by section:**")
        for name, count in included_counts.items():
            st.write(f"- {name}: {count}")
    if current.get("source_errors"):
        st.warning("Some source requests failed:")
        for error in current["source_errors"]:
            st.code(error)
    else:
        st.success("All configured source requests completed.")
