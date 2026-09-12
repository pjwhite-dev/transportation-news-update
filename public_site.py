from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from coverage_history import (
    briefing_stories,
    filter_previously_covered,
    find_previous_coverage,
)
from news_engine import (
    EASTERN,
    SECTION_ORDER,
    TOPIC_SECTIONS,
    automated_record_is_publication_worthy,
    clean_innovative_uas_use,
    infer_innovative_uas_use,
    infer_section,
    sanitize_story_summary,
)
from regulatory_tracker import build_regulatory_tracker


SITE_URL = "https://news.peterjwhite.org"
SITE_TITLE = "Advanced Transportation News Update"

SOURCE_CANONICAL_NAMES = {
    "aol": "AOL",
    "cnbc": "CNBC",
    "dronelife": "DroneLife",
    "military.com": "Military.com",
    "msn": "MSN",
    "new york times": "The New York Times",
    "the new york times": "The New York Times",
    "uas vision": "UAS Vision",
    "uasvision": "UAS Vision",
    "yahoo finance": "Yahoo Finance",
}

SECTION_ANCHORS = {
    "Trump Administration Wins": "wins",
    "Top Developments": "top-developments",
    "UAS and Drones": "uas-drones",
    "UAS Security and C-UAS": "uas-security",
    "Military": "military",
    "eVTOL Integration Pilot Program and AAM": "aam",
    "Autonomous Vehicles": "autonomous-vehicles",
    "Other Advanced Transportation": "other-transportation",
    "International": "international",
    "Federal Actions": "federal-actions",
}

SECTION_NAV_LABELS = {
    "Trump Administration Wins": "Wins",
    "Top Developments": "Top",
    "UAS and Drones": "UAS",
    "UAS Security and C-UAS": "Security",
    "Military": "Military",
    "eVTOL Integration Pilot Program and AAM": "AAM",
    "Autonomous Vehicles": "AVs",
    "Other Advanced Transportation": "Other",
    "International": "International",
    "Federal Actions": "Federal",
}

SAME_DAY_SECTION_PRIORITY = {
    "Military": 0,
    "UAS Security and C-UAS": 1,
    "International": 2,
    "eVTOL Integration Pilot Program and AAM": 3,
    "Autonomous Vehicles": 4,
    "Other Advanced Transportation": 5,
    "Federal Actions": 6,
    "UAS and Drones": 7,
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def edition_date(payload: dict[str, Any]) -> date:
    for key in ("window_end", "generated_at"):
        try:
            return datetime.fromisoformat(str(payload.get(key, ""))).date()
        except ValueError:
            continue
    raise ValueError("Edition has no valid date.")


def load_editions(root: Path) -> dict[date, dict[str, Any]]:
    editions: dict[date, dict[str, Any]] = {}
    for path in sorted((root / "data" / "raw_archive").glob("*.json")):
        try:
            payload = load_json(path)
            day = edition_date(payload)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        editions[day] = {"kind": "automated", "payload": payload}

    latest_raw_path = root / "data" / "latest_raw_news.json"
    if latest_raw_path.exists():
        try:
            payload = load_json(latest_raw_path)
            day = edition_date(payload)
            editions[day] = {"kind": "automated", "payload": payload}
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    for path in sorted((root / "data" / "archive").glob("*.json")):
        try:
            payload = load_json(path)
            day = edition_date(payload)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        editions[day] = {"kind": "editorial", "payload": payload}

    latest_path = root / "data" / "latest_briefing.json"
    if latest_path.exists():
        try:
            payload = load_json(latest_path)
            day = edition_date(payload)
            editions[day] = {"kind": "editorial", "payload": payload}
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    return editions


def prepare_automated_edition(
    payload: dict[str, Any], previous: list[dict[str, Any]]
) -> dict[str, Any]:
    candidate_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in payload.get("articles", []):
        if not automated_record_is_publication_worthy(source):
            continue
        record = dict(source)
        record["section"] = infer_section(record)
        record["innovative_uas_use"] = infer_innovative_uas_use(record)
        group = candidate_groups[record["section"]]
        if len(group) < 16:
            group.append(record)

    previous_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in previous:
        previous_groups[str(record.get("section", ""))].append(record)

    included: list[dict[str, Any]] = []
    prior_suppressed: list[dict[str, Any]] = []
    for section, candidates in candidate_groups.items():
        fresh, repeated = filter_previously_covered(
            candidates, previous_groups.get(section, [])
        )
        included.extend(fresh)
        prior_suppressed.extend(repeated)

    grouped: list[dict[str, Any]] = []
    same_day_consolidated = 0
    for record in included:
        match, _, _ = find_previous_coverage(
            record,
            grouped,
            require_same_section=False,
        )
        if match is None:
            record.setdefault("also_covered", [])
            grouped.append(record)
            continue
        match.setdefault("also_covered", []).append(
            {
                "source": record.get("source", "Related coverage"),
                "url": record.get("url", ""),
            }
        )
        match_section = str(match.get("section", ""))
        record_section = str(record.get("section", ""))
        if SAME_DAY_SECTION_PRIORITY.get(record_section, 99) < (
            SAME_DAY_SECTION_PRIORITY.get(match_section, 99)
        ):
            match["section"] = record_section
        same_day_consolidated += 1

    sections: dict[str, list[dict[str, Any]]] = {
        section: [] for section in TOPIC_SECTIONS
    }
    for record in grouped:
        section = str(record.get("section", ""))
        if section in sections and len(sections[section]) < 6:
            sections[section].append(record)

    return {
        **payload,
        "sections": sections,
        "regulatory_tracker": build_regulatory_tracker(edition_date(payload)),
        "what_to_watch": [],
        "edition_kind": "automated",
        "previously_covered_count": len(prior_suppressed),
        "same_day_consolidated_count": same_day_consolidated,
    }


def prepare_editions(
    editions: dict[date, dict[str, Any]]
) -> dict[date, dict[str, Any]]:
    prepared: dict[date, dict[str, Any]] = {}
    history: list[dict[str, Any]] = []
    for day in sorted(editions):
        cutoff = day - timedelta(days=45)
        history = [
            item
            for item in history
            if str(item.get("coverage_date", "")) >= cutoff.isoformat()
        ]
        entry = editions[day]
        if entry["kind"] == "editorial":
            payload = dict(entry["payload"])
            payload["edition_kind"] = "editorial"
            prepared[day] = payload
            history.extend(briefing_stories(payload))
        else:
            payload = prepare_automated_edition(entry["payload"], history)
            prepared[day] = payload
            for section, items in payload["sections"].items():
                for item in items:
                    record = dict(item)
                    record["section"] = section
                    record["coverage_date"] = day.isoformat()
                    history.append(record)
    return prepared


def _safe_url(value: str) -> str:
    value = str(value or "").strip()
    if value.startswith(("https://", "http://")):
        return html.escape(value, quote=True)
    return "#"


def _format_day(day: date) -> str:
    return day.strftime("%A, %B %d, %Y").replace(" 0", " ")


def _format_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value).astimezone(EASTERN)
        return (
            parsed.strftime("%I:%M %p ET on %B %d, %Y")
            .lstrip("0")
            .replace(" 0", " ")
        )
    except (TypeError, ValueError):
        return str(value or "")


def _relative_prefix(is_archive_page: bool) -> str:
    return "../../" if is_archive_page else ""


def canonical_source(value: str) -> str:
    source = " ".join(str(value or "Source").split())
    return SOURCE_CANONICAL_NAMES.get(source.casefold(), source)


def story_anchor(item: dict[str, Any]) -> str:
    identity = str(item.get("url") or item.get("title") or "story")
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"story-{digest}"


def _glance_items(
    sections: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    all_items: list[dict[str, Any]] = []
    for section in SECTION_ORDER:
        all_items.extend(sections.get(section, []))

    priority: list[dict[str, Any]] = []
    priority.extend(sections.get("Trump Administration Wins", [])[:2])
    priority.extend(sections.get("Top Developments", [])[:6])
    for section in SECTION_ORDER:
        if section not in {"Trump Administration Wins", "Top Developments"}:
            priority.extend(sections.get(section, [])[:1])
    priority.extend(all_items)

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in priority:
        identity = str(item.get("url") or item.get("title") or "").strip().casefold()
        if not identity or identity in seen:
            continue
        seen.add(identity)
        selected.append(item)
        if len(selected) == 12:
            break
    return selected[:8], selected[8:12], len(all_items)


def story_html(item: dict[str, Any]) -> str:
    title = html.escape(str(item.get("title", "Untitled")))
    summary = html.escape(
        sanitize_story_summary(
            str(item.get("title", "")), str(item.get("summary", ""))
        )
    )
    source = html.escape(canonical_source(str(item.get("source", "Source"))))
    date_label = html.escape(str(item.get("date_label", "")))
    summary_markup = f'<p class="story-summary">{summary}</p>' if summary else ""
    related = []
    seen_urls = {item.get("url", "")}
    for related_item in item.get("also_covered", []):
        url = related_item.get("url", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        related.append(
            f'<a href="{_safe_url(url)}">'
            f'{html.escape(canonical_source(str(related_item.get("source", "Related coverage"))))}</a>'
        )
    related_markup = (
        '<p class="related"><strong>Additional coverage:</strong> '
        + " · ".join(related[:4])
        + "</p>"
        if related
        else ""
    )
    use = str(item.get("innovative_uas_use", "")).strip()
    use_markup = (
        f'<p class="innovation-label"><strong>Innovative use</strong> '
        f'{html.escape(use)}</p>'
        if use
        else ""
    )
    win_markup = ""
    if item.get("is_administration_win") and item.get("win_explanation"):
        win_markup = (
            '<aside class="win-callout"><strong>Why this is an Administration win</strong>'
            f'<p>{html.escape(str(item.get("win_explanation", "")))}</p></aside>'
        )
    return f"""
      <article class="story" id="{story_anchor(item)}">
        <h3><a href="{_safe_url(item.get('url', ''))}">{title}</a></h3>
        {summary_markup}
        {use_markup}
        {win_markup}
        <p class="meta">{source}{f' · {date_label}' if date_label else ''}</p>
        {related_markup}
      </article>
    """


def tracker_html(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    rows = []
    for item in items:
        days = item.get("days_remaining")
        days_label = str(days) if isinstance(days, int) else "—"
        prefix = "Closes" if isinstance(days, int) else "Closed"
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('agency', '')))}</td>"
            f'<td><a href="{_safe_url(item.get("source_url", ""))}">'
            f"{html.escape(str(item.get('action', '')))}</a></td>"
            f"<td>{prefix} {html.escape(str(item.get('comment_deadline_label', '')))}</td>"
            f"<td>{days_label}</td>"
            f"<td>{html.escape(str(item.get('status', '')))}</td>"
            "</tr>"
        )
    return """
      <section class="newsletter-section tracker" id="tracker">
        <h2>Regulatory Deadline Tracker</h2>
        <div class="table-scroll" role="region" tabindex="0" aria-label="Regulatory deadlines table"><table>
          <thead><tr><th>Agency</th><th>Action</th><th>Comment period</th><th>Days</th><th>Status</th></tr></thead>
          <tbody>""" + "".join(rows) + """</tbody>
        </table></div>
      </section>
    """


def headline_index_html(sections: dict[str, list[dict[str, Any]]]) -> str:
    groups = []
    top, also, total = _glance_items(sections)
    for bucket_label, bucket_items in (
        ("Top Stories", top),
        ("Also Today", also),
    ):
        if not bucket_items:
            continue
        bucket_ids = {story_anchor(item) for item in bucket_items}
        section_groups = []
        for section in SECTION_ORDER:
            items = [
                item
                for item in sections.get(section, [])
                if story_anchor(item) in bucket_ids
            ]
            if not items:
                continue
            links = "".join(
                f'<li><a href="#{story_anchor(item)}">'
                f'{html.escape(str(item.get("title", "Untitled")))}</a></li>'
                for item in items
            )
            section_groups.append(
                f'<div class="glance-section"><h4>{html.escape(section)}</h4>'
                f'<ul>{links}</ul></div>'
            )
        groups.append(
            f'<div class="glance-column"><h3>{bucket_label}</h3>'
            + "".join(section_groups)
            + "</div>"
        )
    if not groups:
        return ""
    return (
        '<section class="headline-index"><h2>Headlines at a Glance</h2>'
        + "".join(groups)
        + f'<a class="view-all" href="#briefing-sections">View all {total} stories ↓</a>'
        + "</section>"
    )


def section_nav_html(payload: dict[str, Any]) -> str:
    links = ['<a href="#summary">Summary</a>']
    sections = payload.get("sections", {})
    for section in SECTION_ORDER:
        if sections.get(section):
            links.append(
                f'<a href="#{SECTION_ANCHORS[section]}">{SECTION_NAV_LABELS[section]}</a>'
            )
    if payload.get("regulatory_tracker"):
        links.append('<a href="#tracker">Tracker</a>')
    if payload.get("what_to_watch"):
        links.append('<a href="#watch">Watch</a>')
    return (
        '<nav class="section-nav no-copy" aria-label="Edition sections">'
        + "".join(links)
        + "</nav>"
    )


def top_highlights_html(payload: dict[str, Any]) -> str:
    cards = []
    imminent = [
        item
        for item in payload.get("regulatory_tracker", [])
        if isinstance(item.get("days_remaining"), int)
        and 0 <= item["days_remaining"] <= 14
    ]
    if imminent:
        items = "".join(
            f'<li><a href="{_safe_url(item.get("source_url", ""))}">'
            f'{html.escape(str(item.get("action", "")))}</a> — '
            f'{html.escape(str(item.get("comment_deadline_label", "")))} '
            f'({item["days_remaining"]} day'
            f'{"" if item["days_remaining"] == 1 else "s"} remaining)</li>'
            for item in sorted(imminent, key=lambda value: value["days_remaining"])
        )
        cards.append(
            '<aside class="top-highlight"><strong>Imminent regulatory deadlines</strong>'
            f'<ul>{items}</ul></aside>'
        )

    uses = []
    seen = set()
    for items in payload.get("sections", {}).values():
        for item in items:
            value = clean_innovative_uas_use(
                str(item.get("innovative_uas_use", ""))
            )
            identity = value.casefold()
            if value and identity not in seen:
                seen.add(identity)
                uses.append(value)
    if uses:
        cards.append(
            '<aside class="top-highlight"><strong>Innovative UAS uses in today’s briefing</strong>'
            f'<p>{html.escape(" • ".join(uses))}</p></aside>'
        )
    return f'<div class="top-highlights">{"".join(cards)}</div>' if cards else ""


def _email_spacer(height: int) -> str:
    return (
        f'<tr><td height="{height}" style="height:{height}px;line-height:{height}px;'
        'font-size:0">&nbsp;</td></tr>'
    )


def _outlook_story(item: dict[str, Any]) -> str:
    raw_title = str(item.get("title", "Untitled"))
    title = html.escape(raw_title)
    summary = html.escape(
        sanitize_story_summary(raw_title, str(item.get("summary", "")))
    )
    url = _safe_url(item.get("url", ""))
    source = html.escape(canonical_source(str(item.get("source", "Source"))))
    date_label = html.escape(str(item.get("date_label", "")))
    detail_rows = ""
    if summary:
        detail_rows += (
            '<tr><td style="padding:7px 0 0;font-family:Arial,Helvetica,sans-serif;'
            f'font-size:14px;line-height:21px;color:#283640">{summary}</td></tr>'
        )
    use = clean_innovative_uas_use(str(item.get("innovative_uas_use", "")))
    if use:
        detail_rows += (
            '<tr><td style="padding:9px 0 0 10px;border-left:2px solid #B9A45A;'
            'font-family:Arial,Helvetica,sans-serif;font-size:11px;line-height:17px;'
            'color:#626A70"><strong style="text-transform:uppercase;letter-spacing:.3px;'
            f'color:#4F5F69">Innovative use</strong> &nbsp;{html.escape(use)}</td></tr>'
        )
    if item.get("is_administration_win") and item.get("win_explanation"):
        detail_rows += (
            '<tr><td style="padding:10px 0 0"><table role="presentation" width="100%" '
            'cellspacing="0" cellpadding="0" bgcolor="#FFF1ED" style="width:100%;'
            'border-collapse:collapse;background:#FFF1ED;border-left:4px solid #B42318">'
            '<tr><td style="padding:11px 13px;font-family:Arial,Helvetica,sans-serif;'
            'font-size:13px;line-height:19px;color:#57201B"><strong style="font-size:10px;'
            'line-height:14px;letter-spacing:.3px">WHY THIS IS AN ADMINISTRATION WIN</strong><br>'
            f'{html.escape(str(item.get("win_explanation", "")))}</td></tr></table></td></tr>'
        )
    related_links = []
    seen_urls = {str(item.get("url", ""))}
    for related in item.get("also_covered", []):
        related_url = str(related.get("url", ""))
        if not related_url or related_url in seen_urls:
            continue
        seen_urls.add(related_url)
        related_links.append(
            f'<a href="{_safe_url(related_url)}" style="color:#60758A;text-decoration:underline">'
            f'{html.escape(canonical_source(str(related.get("source", "Related coverage"))))}</a>'
        )
    if related_links:
        detail_rows += (
            '<tr><td style="padding:7px 0 0;font-family:Arial,Helvetica,sans-serif;'
            'font-size:11px;line-height:16px;color:#737F89"><strong>Additional coverage:</strong> '
            + ' &nbsp;&bull;&nbsp; '.join(related_links[:4])
            + '</td></tr>'
        )
    return f"""
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
          style="width:100%;border-collapse:collapse">
        <tr><td style="padding:0;font-family:Arial,Helvetica,sans-serif;font-size:18px;
            line-height:24px;font-weight:bold;mso-line-height-rule:exactly">
          <a href="{url}" style="color:#173C5E;text-decoration:underline">{title}</a>
        </td></tr>
        {detail_rows}
        <tr><td style="padding:9px 0 0;font-family:Arial,Helvetica,sans-serif;font-size:11px;
            line-height:16px;color:#707B84">{source}{f' &nbsp;&bull;&nbsp; {date_label}' if date_label else ''}
            &nbsp;&bull;&nbsp; <a href="{url}" style="color:#58738A;text-decoration:underline">Read source</a>
        </td></tr>
        {_email_spacer(18)}
        <tr><td height="1" bgcolor="#DFE5E9" style="height:1px;line-height:1px;font-size:0">&nbsp;</td></tr>
        {_email_spacer(18)}
      </table>
    """


def outlook_email_html(payload: dict[str, Any], day: date) -> str:
    """Return self-contained table HTML that retains its styling in Outlook."""
    sections = payload.get("sections", {})
    summary = html.escape(str(payload.get("executive_summary", "")))
    summary_markup = ""
    if summary:
        summary_markup = (
            '<tr><td style="padding:0 28px"><table role="presentation" width="100%" '
            'cellspacing="0" cellpadding="0" bgcolor="#EAF2F7" style="width:100%;'
            'border-collapse:collapse;background:#EAF2F7;border-left:4px solid #4D7898">'
            '<tr><td style="padding:15px 17px;font-family:Arial,Helvetica,sans-serif">'
            '<div style="font-size:10px;line-height:14px;font-weight:bold;color:#244D6B;'
            'letter-spacing:.4px">EXECUTIVE SUMMARY</div><div style="padding-top:5px;'
            f'font-size:14px;line-height:21px;color:#24323D">{summary}</div></td></tr>'
            f'</table></td></tr>{_email_spacer(22)}'
        )
    headline_groups = []
    top, also, _ = _glance_items(sections)
    for bucket_label, bucket_items in (
        ("Top Stories", top),
        ("Also Today", also),
    ):
        if not bucket_items:
            continue
        bucket_ids = {story_anchor(item) for item in bucket_items}
        section_rows = []
        for section in SECTION_ORDER:
            items = [
                item
                for item in sections.get(section, [])
                if story_anchor(item) in bucket_ids
            ]
            if not items:
                continue
            links = "".join(
                '<tr><td width="14" valign="top" style="width:14px;padding:1px 0 5px;'
                'font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:17px;color:#5C7182">'
                '&#8226;</td><td valign="top" style="padding:0 0 5px;font-family:Arial,Helvetica,sans-serif;'
                f'font-size:12px;line-height:17px"><a href="{_safe_url(item.get("url", ""))}" '
                'style="color:#294F6D;text-decoration:none">'
                f'{html.escape(str(item.get("title", "Untitled")))}</a></td></tr>'
                for item in items
            )
            section_rows.append(
                '<tr><td style="padding:4px 0 3px;font-family:Arial,Helvetica,sans-serif;'
                'font-size:9px;line-height:13px;font-weight:bold;color:#73828D;letter-spacing:.25px">'
                f'{html.escape(section.upper())}</td></tr><tr><td><table role="presentation" '
                'width="100%" cellspacing="0" cellpadding="0" style="width:100%;'
                f'border-collapse:collapse">{links}</table></td></tr>'
            )
        headline_groups.append(
            '<tr><td style="padding:0 0 4px;font-family:Arial,Helvetica,sans-serif;font-size:10px;'
            f'line-height:14px;font-weight:bold;color:#244D6B;letter-spacing:.3px">{bucket_label.upper()}'
            '</td></tr><tr><td style="padding:0 0 9px"><table role="presentation" width="100%" '
            f'cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse">{"".join(section_rows)}'
            '</table></td></tr>'
        )
    headlines_markup = ""
    if headline_groups:
        headlines_markup = (
            '<tr><td style="padding:0 28px"><table role="presentation" width="100%" cellspacing="0" '
            'cellpadding="0" bgcolor="#FAFCFD" style="width:100%;border-collapse:collapse;'
            'background:#FAFCFD;border:1px solid #DBE4EA"><tr><td style="padding:13px 15px 8px">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            'style="width:100%;border-collapse:collapse"><tr><td style="padding:0 0 9px;'
            'font-family:Arial,Helvetica,sans-serif;font-size:11px;line-height:15px;font-weight:bold;'
            'color:#244D6B;letter-spacing:.4px">HEADLINES AT A GLANCE</td></tr>'
            + "".join(headline_groups)
            + f'</table></td></tr></table></td></tr>{_email_spacer(25)}'
        )
    section_markup = []
    for section in SECTION_ORDER:
        items = sections.get(section, [])
        if not items:
            continue
        heading_color = "#8C241E" if section == "Trump Administration Wins" else "#173C5E"
        section_markup.append(
            '<tr><td style="padding:0 28px"><table role="presentation" width="100%" '
            'cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse">'
            f'<tr><td style="padding:0 0 9px;font-family:Arial,Helvetica,sans-serif;font-size:22px;'
            f'line-height:27px;font-weight:bold;color:{heading_color};border-bottom:2px solid #CBD6DE">'
            f'{html.escape(section)}</td></tr>{_email_spacer(16)}<tr><td>'
            + "".join(_outlook_story(item) for item in items)
            + f'</td></tr></table></td></tr>{_email_spacer(8)}'
        )
    tracker_markup = _outlook_tracker(payload.get("regulatory_tracker", []))
    watch_markup = _outlook_watch(payload.get("what_to_watch", []))
    return f"""<!doctype html><html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
    <head><meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
      <!--[if mso]><style>body,table,td,a,p,span{{font-family:Arial,Helvetica,sans-serif!important}}
      table{{border-collapse:collapse!important}}</style><![endif]--></head>
    <body style="Margin:0;padding:0;background:#FFFFFF">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" bgcolor="#FFFFFF"
          style="width:100%;border-collapse:collapse;background:#FFFFFF"><tr><td align="center">
        <table role="presentation" width="720" cellspacing="0" cellpadding="0" align="center"
            bgcolor="#FFFFFF" style="width:720px;max-width:720px;border-collapse:collapse;background:#FFFFFF">
          <tr><td bgcolor="#153A5A" style="padding:24px 28px 22px;background:#153A5A;
              font-family:Arial,Helvetica,sans-serif"><div style="font-size:30px;line-height:35px;
              font-weight:bold;color:#FFFFFF">Advanced Transportation News Update</div>
              <div style="padding-top:7px;font-size:14px;line-height:19px;color:#DCE8F0">
              {html.escape(_format_day(day))}</div></td></tr>{_email_spacer(18)}
          {summary_markup}{headlines_markup}{"".join(section_markup)}{tracker_markup}{watch_markup}
          <tr><td style="padding:0 28px 24px;font-family:Arial,Helvetica,sans-serif;font-size:11px;
              line-height:16px;color:#7B848C;border-top:1px solid #DFE5E9">
              Public source, AI-assisted news update.</td></tr>
        </table></td></tr></table></body></html>"""


def _outlook_tracker(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    rows = []
    for item in items:
        days = item.get("days_remaining")
        prefix = "Closes" if isinstance(days, int) else "Closed"
        values = (
            html.escape(str(item.get("agency", ""))),
            f'<a href="{_safe_url(item.get("source_url", ""))}" style="color:#173C5E;'
            f'text-decoration:underline;font-weight:bold">{html.escape(str(item.get("action", "")))}</a>',
            f'{prefix} {html.escape(str(item.get("comment_deadline_label", "")))}',
            str(days) if isinstance(days, int) else "—",
            html.escape(str(item.get("status", ""))),
        )
        cells = "".join(
            '<td valign="top" style="padding:9px 7px;border-bottom:1px solid #E1E6EA;'
            f'font-family:Arial,Helvetica,sans-serif;font-size:10px;line-height:15px;color:#4F5F6C">{value}</td>'
            for value in values
        )
        rows.append(f"<tr>{cells}</tr>")
    headers = "".join(
        f'<td style="padding:7px;font-family:Arial,Helvetica,sans-serif;font-size:9px;font-weight:bold;'
        f'color:#5D6B78">{label}</td>'
        for label in ("AGENCY", "ACTION", "COMMENT PERIOD", "DAYS", "STATUS")
    )
    return (
        '<tr><td style="padding:0 28px"><table role="presentation" width="100%" cellspacing="0" '
        'cellpadding="0" style="width:100%;border-collapse:collapse"><tr><td style="padding:0 0 9px;'
        'font-family:Arial,Helvetica,sans-serif;font-size:22px;line-height:27px;font-weight:bold;'
        'color:#173C5E;border-bottom:2px solid #CBD6DE">Regulatory Deadline Tracker</td></tr>'
        f'{_email_spacer(14)}<tr><td><table role="presentation" width="100%" cellspacing="0" '
        f'cellpadding="0" style="width:100%;border-collapse:collapse;table-layout:fixed"><tr bgcolor="#F3F6F8">{headers}</tr>'
        + "".join(rows)
        + f'</table></td></tr></table></td></tr>{_email_spacer(24)}'
    )


def _outlook_watch(items: list[str]) -> str:
    if not items:
        return ""
    rows = "".join(
        '<tr><td width="16" valign="top" style="width:16px;padding:1px 0 7px;font-family:Arial,Helvetica,sans-serif;'
        'font-size:14px;line-height:20px;color:#48657D">&#8226;</td><td valign="top" style="padding:0 0 7px;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:20px;color:#252B31">{html.escape(str(item))}</td></tr>'
        for item in items
    )
    return (
        '<tr><td style="padding:0 28px"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        'style="width:100%;border-collapse:collapse"><tr><td style="padding:0 0 9px;font-family:Arial,Helvetica,sans-serif;'
        'font-size:22px;line-height:27px;font-weight:bold;color:#173C5E;border-bottom:2px solid #CBD6DE">What to Watch</td></tr>'
        f'{_email_spacer(14)}<tr><td><table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        f'style="width:100%;border-collapse:collapse">{rows}</table></td></tr></table></td></tr>{_email_spacer(20)}'
    )


def edition_content(payload: dict[str, Any], day: date) -> str:
    sections = payload.get("sections", {})
    kind = payload.get("edition_kind", "automated")
    if kind == "editorial":
        intro = (
            '<section class="executive-summary" id="summary"><h2>Executive Summary</h2>'
            f'<p>{html.escape(str(payload.get("executive_summary", "")))}</p></section>'
        )
        status = "Complete AI-assisted edition"
    else:
        shown = sum(len(items) for items in sections.values())
        status = "Automatically updated public-source headline edition"
        intro = f"""
          <section class="automated-note" id="summary">
            <strong>{shown} current headlines</strong>
            <span>Grouped by subject and checked against earlier editions.</span>
          </section>
        """

    section_markup = []
    for section in SECTION_ORDER:
        items = sections.get(section, [])
        if not items:
            continue
        section_class = (
            " newsletter-section--wins"
            if section == "Trump Administration Wins"
            else ""
        )
        section_markup.append(
            f'<section class="newsletter-section{section_class}" id="{SECTION_ANCHORS[section]}">'
            f'<h2>{html.escape(section)}</h2>'
            + "".join(story_html(item) for item in items)
            + "</section>"
        )

    tracker = tracker_html(payload.get("regulatory_tracker", []))
    watch = payload.get("what_to_watch", [])
    watch_markup = ""
    if watch:
        watch_markup = (
            '<section class="newsletter-section what-to-watch" id="watch"><h2>What to Watch</h2><ol class="watch">'
            + "".join(f"<li>{html.escape(str(item))}</li>" for item in watch)
            + "</ol></section>"
        )

    return f"""
      <div class="edition-heading">
        <p class="eyebrow">{html.escape(status)}</p>
        <h1>{html.escape(_format_day(day))}</h1>
        <p class="coverage">Coverage through {_format_time(payload.get('window_end', ''))}</p>
      </div>
      {section_nav_html(payload)}
      {intro}
      {top_highlights_html(payload)}
      {headline_index_html(sections)}
      <div class="briefing-sections" id="briefing-sections">
        {''.join(section_markup)}
        {tracker}
        {watch_markup}
      </div>
      <footer>Public source, AI-assisted news update.</footer>
    """


def page_shell(
    *,
    body: str,
    title: str,
    canonical_path: str,
    prefix: str = "",
    show_copy: bool = False,
) -> str:
    copy_controls = ""
    if show_copy:
        copy_controls = (
            '<button type="button" data-copy-edition>Copy for email</button>'
            '<span class="copy-status" role="status" '
            'aria-live="polite" data-copy-status></span>'
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Daily public-source advanced transportation headlines and archived editions.">
  <title>{html.escape(title)}</title>
  <link rel="canonical" href="{SITE_URL}{canonical_path}">
  <link rel="stylesheet" href="{prefix}assets/site.css">
</head>
<body>
  <header class="site-header no-copy">
    <div class="masthead-inner">
      <a class="brand" href="{prefix}" aria-label="{SITE_TITLE}">
        <span>Advanced Transportation</span><small>News Update</small>
      </a>
      <div class="masthead-tools">
        <nav aria-label="Primary navigation">
          <a href="{prefix}">Latest</a>
          <a href="{prefix}archive/">Archive</a>
        </nav>
        {copy_controls}
      </div>
    </div>
  </header>
  {body}
  <script src="{prefix}assets/site.js" defer></script>
</body>
</html>
"""


def edition_page(payload: dict[str, Any], day: date, archived: bool) -> str:
    prefix = _relative_prefix(archived)
    content = edition_content(payload, day)
    email_content = base64.b64encode(
        outlook_email_html(payload, day).encode("utf-8")
    ).decode("ascii")
    body = f"""
      <main>
        <div class="newsletter" data-edition-content
            data-outlook-email-b64="{email_content}">{content}</div>
      </main>
    """
    canonical = f"/archive/{day.isoformat()}/" if archived else "/"
    return page_shell(
        body=body,
        title=f"{SITE_TITLE} — {_format_day(day)}",
        canonical_path=canonical,
        prefix=prefix,
        show_copy=True,
    )


def archive_page(editions: dict[date, dict[str, Any]]) -> str:
    cards = []
    for day in sorted(editions, reverse=True):
        payload = editions[day]
        story_count = sum(
            len(items) for items in payload.get("sections", {}).values()
        )
        label = (
            "Editorial edition"
            if payload.get("edition_kind") == "editorial"
            else "Automated headline edition"
        )
        cards.append(
            f"""
            <li>
              <a href="{day.isoformat()}/">
                <strong>{html.escape(_format_day(day))}</strong>
                <span>{html.escape(label)} · {story_count} stories</span>
              </a>
            </li>
            """
        )
    body = f"""
      <main class="archive-page">
        <div class="archive-heading">
          <p class="eyebrow">Past editions</p>
          <h1>News Archive</h1>
          <p>Browse the daily editions and open any date to copy it for email.</p>
        </div>
        <ol class="archive-list">{''.join(cards)}</ol>
      </main>
    """
    return page_shell(
        body=body,
        title=f"Archive — {SITE_TITLE}",
        canonical_path="/archive/",
        prefix="../",
    )


SITE_CSS = """
:root{--navy:#123752;--navy-dark:#08283f;--ink:#202a32;--muted:#687985;--line:#d9e1e6;--paper:#fff;--wash:#f3f6f8;--sand:#f7f2e7;--red:#8c2f2a;--wide:1120px;--reader:760px;color-scheme:light}
*{box-sizing:border-box;min-width:0}
html{font-size:16px;background:#eef2f4;scroll-behavior:smooth}
body{margin:0;color:var(--ink);font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Arial,sans-serif;line-height:1.58;-webkit-font-smoothing:antialiased}
a{color:#15567e;text-underline-offset:3px;overflow-wrap:anywhere}
a:focus-visible,button:focus-visible,.table-scroll:focus-visible{outline:3px solid #d2a92f;outline-offset:3px}
.site-header{background:var(--navy-dark);color:#fff;border-bottom:3px solid #c9a13a}
.masthead-inner{width:min(var(--wide),calc(100% - 40px));min-height:102px;margin:auto;display:flex;align-items:center;justify-content:space-between;gap:34px}
.site-header a{color:#fff;text-decoration:none}.brand{display:flex;flex-direction:column;line-height:1.05;text-transform:uppercase}.brand span{font-size:1.2rem;font-weight:800;letter-spacing:.075em}.brand small{margin-top:7px;font-size:.72rem;font-weight:700;letter-spacing:.28em;color:#cbd9e3}.masthead-tools{display:flex;align-items:center;justify-content:flex-end;gap:24px}.site-header nav{display:flex;gap:20px;font-size:.82rem;font-weight:700}.site-header nav a:hover{text-decoration:underline}.masthead-tools button{min-height:42px;appearance:none;border:1px solid #88a0b2;border-radius:5px;background:transparent;color:#fff;padding:9px 13px;font:inherit;font-size:.8rem;font-weight:750;cursor:pointer}.masthead-tools button:hover{background:#153c58}.copy-status{position:absolute;margin-top:68px;font-size:.75rem;color:#d8e9d8}
main{width:min(var(--wide),calc(100% - 40px));margin:30px auto 72px}
.newsletter{width:100%;overflow:visible;background:var(--paper);border:1px solid #dfe5e9;box-shadow:0 16px 44px rgba(26,51,70,.07);padding:50px clamp(28px,6vw,72px) 42px}
.edition-heading,.executive-summary,.automated-note,.newsletter-section:not(.tracker):not(.what-to-watch),footer{width:min(var(--reader),100%);margin-left:auto;margin-right:auto}
.edition-heading{border-top:6px solid var(--navy);padding:20px 0 22px;margin-bottom:0}.eyebrow{text-transform:uppercase;letter-spacing:.13em;font-size:.7rem;font-weight:800;color:#557286;margin:0 0 8px}.edition-heading h1,.archive-heading h1{font-size:clamp(2.15rem,5vw,2.9rem);line-height:1.05;color:var(--navy-dark);letter-spacing:-.035em;margin:0;overflow-wrap:break-word}.coverage{font-size:.79rem;color:var(--muted);margin:9px 0 0}
.section-nav{position:sticky;top:0;z-index:10;display:flex;gap:4px;overflow-x:auto;white-space:nowrap;margin:0 0 34px;padding:7px;background:rgba(255,255,255,.97);border-top:1px solid var(--line);border-bottom:1px solid var(--line);box-shadow:0 5px 15px rgba(24,52,70,.06);scrollbar-width:thin}.section-nav a{flex:0 0 auto;padding:7px 9px;color:#425c6e;text-decoration:none;font-size:.72rem;font-weight:750;border-radius:4px}.section-nav a:hover{background:var(--wash);color:var(--navy-dark)}
.executive-summary{background:#edf4f8;border-left:4px solid #4d7898;padding:24px 26px;margin-bottom:34px}.executive-summary h2,.headline-index>h2{font-size:.72rem;text-transform:uppercase;letter-spacing:.11em;color:#244d6b;margin:0 0 9px}.executive-summary p{font-size:1.075rem;line-height:1.65;margin:0;color:#253944}
.automated-note{display:flex;justify-content:space-between;gap:20px;background:var(--sand);border-left:4px solid #b89a50;padding:15px 17px;margin-bottom:30px;font-size:.88rem}.automated-note span{color:#675d43}
.top-highlights{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin:0 0 34px}.top-highlight{background:var(--sand);border-top:3px solid #b89a50;padding:16px 18px;margin:0;font-size:.84rem;color:#4c493f}.top-highlight>strong{text-transform:uppercase;letter-spacing:.075em;font-size:.69rem;color:#5c5239}.top-highlight p,.top-highlight ul{margin:7px 0 0}.top-highlight ul{padding-left:20px}.top-highlight:only-child{grid-column:1/-1}
.headline-index{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));column-gap:40px;row-gap:2px;border:1px solid var(--line);background:#fafcfd;padding:23px 25px 18px;margin:0 0 54px}.headline-index>h2{grid-column:1/-1;margin-bottom:10px}.headline-index>div{margin:0}.headline-index h3{font-size:.69rem;text-transform:uppercase;letter-spacing:.075em;color:#607482;margin:0 0 7px}.headline-index h4{font-size:.74rem;line-height:1.3;color:#243b4b;margin:12px 0 6px}.headline-index ul{font-size:.855rem;line-height:1.45;margin:0;padding-left:18px}.headline-index li{margin:0 0 6px}.headline-index a{text-decoration:none}.headline-index a:hover{text-decoration:underline}.headline-index .view-all{grid-column:1/-1;width:max-content;margin-top:12px;font-size:.78rem;font-weight:750}
.newsletter-section{margin-bottom:60px;scroll-margin-top:64px}.newsletter-section>h2{font-size:1.65rem;line-height:1.18;color:var(--navy-dark);border-bottom:2px solid #cbd6de;padding-bottom:10px;margin:0 0 22px;letter-spacing:-.025em}.story{border-bottom:1px solid #e1e6ea;padding:0 0 24px;margin:0 0 24px;scroll-margin-top:62px}.story:last-child{margin-bottom:0}.story h3{font-size:1.2rem;line-height:1.35;margin:0 0 8px;letter-spacing:-.012em}.story h3 a{font-weight:725;text-decoration:none;color:#173c5e}.story h3 a:hover{text-decoration:underline}.story-summary{font-size:.975rem;line-height:1.58;margin:0 0 10px;color:#303d45}.meta,.related{font-size:.755rem;line-height:1.5;color:var(--muted);margin:7px 0 0}.innovation-label{margin:10px 0 0;padding-left:10px;border-left:2px solid #b9a45a;color:#66727a;font-size:.755rem;line-height:1.5}.innovation-label strong{text-transform:uppercase;letter-spacing:.055em;font-size:.67rem;color:#52616a;margin-right:6px}.win-callout{border-top:1px solid #d9b8b3;padding:11px 0 0;margin:13px 0 0;font-size:.82rem;color:#653631}.win-callout>strong{text-transform:uppercase;letter-spacing:.055em;font-size:.68rem}.win-callout p{margin:5px 0 0}.newsletter-section--wins{width:min(860px,100%)!important;background:#fff7f5;border-left:4px solid var(--red);padding:26px 28px 2px}.newsletter-section--wins>h2{color:#732822;border-bottom-color:#dfc2be}
.tracker{background:#f4f7f9;border-top:5px solid var(--navy);padding:26px 28px 24px}.tracker>h2{border:0;margin-bottom:15px}.table-scroll{width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}.tracker table{border-collapse:collapse;width:100%;font-size:.79rem}.tracker th{text-align:left;background:#e6edf1;color:#4d6371;text-transform:uppercase;font-size:.65rem;letter-spacing:.04em}.tracker th,.tracker td{padding:12px 10px;border-bottom:1px solid #d4dde3;vertical-align:top}.tracker th:nth-child(2){width:34%}.tracker th:nth-child(4){width:7%}.tracker td:nth-child(4){font-weight:800;color:#294f6d}.tracker tr:last-child td{border-bottom:0}
.what-to-watch{width:100%!important;background:#edf3f7;border-top:4px solid #5a7b92;padding:28px 32px 22px}.what-to-watch>h2{border:0;margin-bottom:14px}.watch{margin:0;padding-left:24px;columns:2;column-gap:42px}.watch li{break-inside:avoid;margin:0 0 11px;padding-left:4px;font-size:.95rem}.watch li::marker{font-weight:800;color:#526f84}footer{border-top:1px solid var(--line);padding-top:17px;color:#7b848c;font-size:.73rem}
.archive-page{width:min(800px,calc(100% - 40px))}.archive-heading{background:#fff;border:1px solid var(--line);padding:42px 46px;margin-bottom:16px;box-shadow:0 12px 35px rgba(26,51,70,.06)}.archive-heading p:last-child{margin-bottom:0;color:var(--muted)}.archive-list{list-style:none;padding:0;margin:0;display:grid;gap:10px}.archive-list a{display:flex;justify-content:space-between;align-items:center;gap:20px;background:#fff;border:1px solid var(--line);padding:18px 21px;text-decoration:none;color:var(--navy)}.archive-list a:hover{border-color:#7892a4;box-shadow:0 5px 20px rgba(26,51,70,.07)}.archive-list span{font-size:.82rem;color:var(--muted);text-align:right}
@media(max-width:760px){.masthead-inner{min-height:auto;padding:16px 0;align-items:flex-start;flex-wrap:wrap;gap:14px}.brand span{font-size:1rem}.brand small{font-size:.65rem}.masthead-tools{width:100%;justify-content:space-between;gap:12px}.masthead-tools button{min-height:44px}.copy-status{margin-top:50px;right:20px}.newsletter{padding:34px 22px}.top-highlights,.headline-index{grid-template-columns:1fr}.headline-index>h2,.headline-index .view-all{grid-column:1}.headline-index>div+div{margin-top:12px}.tracker{padding:23px 20px}.tracker table{min-width:760px}.what-to-watch{padding:25px 22px 18px}.watch{columns:1}.archive-heading{padding:32px 25px}}
@media(max-width:440px){main,.archive-page{width:calc(100% - 20px);margin-top:12px}.masthead-inner{width:calc(100% - 28px)}.site-header nav{gap:15px}.newsletter{padding:27px 18px}.edition-heading{padding-top:17px}.edition-heading h1,.archive-heading h1{font-size:1.9rem}.section-nav{margin-left:-10px;margin-right:-10px;padding-left:10px}.automated-note,.archive-list a{align-items:flex-start;flex-direction:column;gap:4px}.archive-list span{text-align:left}.headline-index{padding:19px 17px 15px}.executive-summary{padding:20px}.newsletter-section>h2{font-size:1.4rem}.story h3{font-size:1.12rem}.newsletter-section--wins{padding:22px 19px 1px}.tracker,.what-to-watch{margin-left:-8px;width:calc(100% + 16px)!important}}
@media print{html{background:#fff}.site-header,.no-copy{display:none!important}main{width:100%;margin:0}.newsletter{border:0;box-shadow:none;padding:0}}
""".strip() + "\n"


SITE_JS = r"""
(function () {
  const button = document.querySelector('[data-copy-edition]');
  const content = document.querySelector('[data-edition-content]');
  const status = document.querySelector('[data-copy-status]');
  if (!button || !content) return;

  button.addEventListener('click', async function () {
    let rich = content.innerHTML;
    const encoded = content.dataset.outlookEmailB64;
    if (encoded) {
      const bytes = Uint8Array.from(atob(encoded), function (character) {
        return character.charCodeAt(0);
      });
      rich = new TextDecoder('utf-8').decode(bytes);
    }
    const plain = content.innerText.trim();
    try {
      if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
        await navigator.clipboard.write([new ClipboardItem({
          'text/html': new Blob([rich], {type: 'text/html'}),
          'text/plain': new Blob([plain], {type: 'text/plain'})
        })]);
      } else {
        await navigator.clipboard.writeText(plain);
      }
      status.textContent = 'Copied.';
      setTimeout(function () { status.textContent = ''; }, 2500);
    } catch (error) {
      status.textContent = 'Copy was blocked. Select the edition and copy it manually.';
    }
  });
})();
""".strip() + "\n"


def build_public_site(root: Path, output: Path) -> dict[str, int]:
    editions = prepare_editions(load_editions(root))
    if not editions:
        raise RuntimeError("No archived news editions are available.")

    if output.exists():
        shutil.rmtree(output)
    (output / "assets").mkdir(parents=True)
    (output / "archive").mkdir(parents=True)

    editorial_days = [
        day
        for day, payload in editions.items()
        if payload.get("edition_kind") == "editorial"
    ]
    # A raw collection is an input, not a publishable edition. Keep it in the
    # archive for continuity, but never let it replace the last complete briefing.
    latest_day = max(editorial_days) if editorial_days else max(editions)
    (output / "index.html").write_text(
        edition_page(editions[latest_day], latest_day, archived=False),
        encoding="utf-8",
    )
    (output / "archive" / "index.html").write_text(
        archive_page(editions), encoding="utf-8"
    )
    for day, payload in editions.items():
        edition_dir = output / "archive" / day.isoformat()
        edition_dir.mkdir(parents=True)
        (edition_dir / "index.html").write_text(
            edition_page(payload, day, archived=True), encoding="utf-8"
        )

    (output / "assets" / "site.css").write_text(SITE_CSS, encoding="utf-8")
    (output / "assets" / "site.js").write_text(SITE_JS, encoding="utf-8")
    (output / "CNAME").write_text("news.peterjwhite.org\n", encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    return {"edition_count": len(editions), "latest_day": latest_day.toordinal()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the public news archive.")
    parser.add_argument("--output", type=Path, default=Path("_site"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    result = build_public_site(root, args.output.resolve())
    latest = date.fromordinal(result["latest_day"]).isoformat()
    print(f"Built {result['edition_count']} editions; latest is {latest}.")


if __name__ == "__main__":
    main()
