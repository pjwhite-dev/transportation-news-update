from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import re
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
    sanitize_executive_summary_citations,
    sanitize_story_summary,
)
from market_watch import load_snapshot as load_market_snapshot
from regulatory_tracker import build_regulatory_tracker


SITE_URL = "https://news.peterjwhite.org"
SITE_TITLE = "Advanced Transportation Update"

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
    "Federal Policy & Implementation": "federal-policy",
    "UAS / Drones": "uas-drones",
    "Counter-UAS / Airspace Security": "uas-security",
    "AAM / eVTOL / Advanced Aviation": "aam",
    "UTM / Airspace Integration": "utm",
    "Autonomous Vehicles": "autonomous-vehicles",
    "Robotics": "robotics",
    "Civil Supersonics / High-Speed Aviation": "supersonics",
    "Advanced Rail / High-Speed Rail": "rail",
    "UxS / Maritime / Ground Robotics": "uxs",
    "Surface Transportation Reauthorization": "reauthorization",
    "Other Advanced Transportation": "other-transportation",
    "Military UAS": "military",
    "International Security": "international-security",
    "Federal Register": "federal-register",
}

SECTION_NAV_LABELS = {section: section.split(" /")[0].split(" &")[0] for section in SECTION_ANCHORS}

SAME_DAY_SECTION_PRIORITY = {
    "Military UAS": 0,
    "Counter-UAS / Airspace Security": 1,
    "International Security": 2,
    "AAM / eVTOL / Advanced Aviation": 3,
    "Autonomous Vehicles": 4,
    "Other Advanced Transportation": 5,
    "Federal Policy & Implementation": 6,
    "UAS / Drones": 7,
}

LEGACY_SECTIONS = {
    "Trump Administration Wins": "Federal Policy & Implementation",
    "Top Developments": "Other Advanced Transportation",
    "UAS and Drones": "UAS / Drones",
    "UAS Security and C-UAS": "Counter-UAS / Airspace Security",
    "Military": "Military UAS",
    "eVTOL Integration Pilot Program and AAM": "AAM / eVTOL / Advanced Aviation",
    "International": "Other Advanced Transportation",
    "Federal Actions": "Federal Policy & Implementation",
}


def display_sections(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Retain links from archived editions created under the former taxonomy."""
    result = {section: [] for section in SECTION_ORDER}
    seen: set[str] = set()
    for old_section, items in payload.get("sections", {}).items():
        for item in items:
            identity = str(item.get("url") or item.get("id") or item.get("title"))
            if identity in seen:
                continue
            seen.add(identity)
            target = LEGACY_SECTIONS.get(old_section, old_section)
            if target == "Other Advanced Transportation":
                target = infer_section(item)
            result.setdefault(target, []).append(item)
    return result


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
            payload["sections"] = display_sections(payload)
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


def summary_paragraphs(payload: dict[str, Any]) -> list[str]:
    """Render the final AI summary as inline HTML paragraphs without allowing arbitrary HTML.

    Labeled links to known story URLs are kept. A link whose label is just a number is a source
    citation and renders as a small superscript right after the sentence it supports.
    """
    summary = sanitize_executive_summary_citations(
        str(payload.get("executive_summary", ""))
    )
    allowed = {
        str(item.get("url", ""))
        for items in payload.get("sections", {}).values()
        for item in items
        if str(item.get("url", "")).startswith(("https://", "http://"))
    }
    pattern = re.compile(r"\[([^\]]{1,120})\]\((https?://[^\s)]+)\)")
    paragraphs: list[str] = []
    for paragraph in summary.split("\n\n"):
        parts: list[str] = []
        cursor = 0
        for match in pattern.finditer(paragraph):
            before = paragraph[cursor:match.start()]
            label, url = match.groups()
            citation = label.strip().isdigit()
            if citation:
                before = before.rstrip(" ")
            parts.append(rich(before))
            if url in allowed and citation:
                parts.append(
                    f'<sup class="cite"><a href="{_safe_url(url)}" style="color:#174F86;text-decoration:none" '
                    f'aria-label="Source {html.escape(label)}">{html.escape(label)}</a></sup>'
                )
            elif url in allowed:
                parts.append(
                    f'<a href="{_safe_url(url)}" style="color:#174F86;text-decoration:underline">'
                    f'{html.escape(label)}</a>'
                )
            elif not citation:
                parts.append(html.escape(label))
            cursor = match.end()
        parts.append(rich(paragraph[cursor:]))
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def summary_html(payload: dict[str, Any]) -> str:
    """Single inline block (email): paragraphs separated by blank lines."""
    return "<br><br>".join(summary_paragraphs(payload))


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
    mapped = SOURCE_CANONICAL_NAMES.get(source.casefold())
    if mapped:
        return mapped
    from editorial_checks import looks_like_url, source_label

    return source_label(source) if looks_like_url(source) else source


_ITALIC = re.compile(r"\*([^*\n]{3,140})\*")


def rich(text: str) -> str:
    """Escape text, then turn *Title* markers (executive-order titles) into italics with a dagger."""
    return _ITALIC.sub(lambda m: f"<em>{m.group(1)}</em><sup class=\"fn\">&dagger;</sup>", html.escape(text))


def eo_note(text: str) -> str:
    """Footnote giving the executive-order number for each italicized title in ``text``."""
    from news_engine import EO_DISPLAY_NAMES

    numbers = {name: number.replace("EO ", "Executive Order ") for number, name in EO_DISPLAY_NAMES.items()}
    notes = [f"<em>{html.escape(name)}</em> is {numbers[name]}." for name in dict.fromkeys(_ITALIC.findall(text)) if name in numbers]
    return f'<p class="footnote">&dagger; {" ".join(notes)}</p>' if notes else ""


_TITLE_SUFFIX = re.compile(r"\s+[-–—|]\s+([^-–—|]{2,40})$")


def display_title(item: dict[str, Any]) -> str:
    """Headline without a trailing " - Publisher" tag (the source is shown separately)."""
    title = " ".join(str(item.get("title", "Untitled")).split())
    match = _TITLE_SUFFIX.search(title)
    if not match:
        return title
    suffix = match.group(1).strip().casefold()
    source = canonical_source(str(item.get("source", ""))).casefold()
    short = len(suffix.split()) <= 3
    if suffix and (suffix in source or source in suffix or (short and suffix.replace(" ", "") in source.replace(" ", ""))):
        return title[: match.start()].rstrip()
    return title


def story_anchor(item: dict[str, Any]) -> str:
    identity = str(item.get("url") or item.get("title") or "story")
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"story-{digest}"


def _glance_groups(
    sections: dict[str, list[dict[str, Any]]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Return every included headline once, grouped by its final section."""
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    seen: set[str] = set()
    for section in SECTION_ORDER:
        items: list[dict[str, Any]] = []
        for item in sections.get(section, []):
            identity = str(item.get("id") or item.get("url") or item.get("title") or "").strip().casefold()
            if not identity or identity in seen:
                continue
            seen.add(identity)
            items.append(item)
        if items:
            groups.append((section, items))
    return groups


def story_html(item: dict[str, Any]) -> str:
    title = html.escape(display_title(item))
    summary_text = sanitize_story_summary(str(item.get("title", "")), str(item.get("summary", "")))
    summary = rich(summary_text)
    source = html.escape(canonical_source(str(item.get("source", "Source"))))
    date_label = html.escape(str(item.get("date_label", "")))
    summary_markup = f'<p class="story-summary">{summary}</p>{eo_note(summary_text)}' if summary else ""
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
    use_markup = '<span class="innovation-label">Innovative Drone Use</span>' if use else ""
    return f"""
      <article class="story" id="{story_anchor(item)}"><div>
        <h3><a href="{_safe_url(item.get('url', ''))}">{title}</a>{use_markup}</h3>
        {summary_markup}
        <p class="meta">{source}{f' · {date_label}' if date_label else ''}</p>
        {related_markup}
      </div></article>
    """


def _plural(count: int, word: str) -> str:
    if count == 1:
        return f"{count} {word}"
    plural = word[:-1] + "ies" if word.endswith("y") and not word.endswith(("ay", "ey", "oy")) else word + "s"
    return f"{count} {plural}"


def section_label(section: str) -> str:
    return SECTION_NAV_LABELS.get(section, section)


def ordered_stories(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for _, items in _glance_groups(payload.get("sections", {}))
        for item in items
    ]


def lead_story(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The most important story of the day (first one wins a tie); None for an empty edition."""
    stories = ordered_stories(payload)
    if not stories:
        return None
    return max(stories, key=lambda item: int(item.get("importance", 0) or 0))


_MONTH_DAY = re.compile(
    r"^((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2})\s*[:,–—-]+\s*(.+)$"
)


def tracker_html(items: list[dict[str, Any]]) -> str:
    """A quiet, collapsed docket at the foot of the edition; the summary line carries the news."""
    if not items:
        return ""
    def next_info(item: dict[str, Any]) -> dict[str, Any] | None:
        """The item's next date; editions saved before the docket redesign carry only a comment deadline."""
        if isinstance(item.get("next_date"), dict):
            return item["next_date"]
        if isinstance(item.get("days_remaining"), int) and item.get("comment_deadline"):
            return {"date": str(item["comment_deadline"]), "label": f"{item.get('comment_deadline_label', '')} · Comments due",
                    "kind": "deadline", "days_remaining": item["days_remaining"]}
        return None

    dated = [item for item in items if next_info(item)]
    if dated:
        nxt = min(dated, key=lambda item: next_info(item)["date"])
        info = next_info(nxt)
        headline = f"Next date: {html.escape(info['label'].replace(' · ', ' — '))} — {html.escape(str(nxt.get('agency', '')))}"
        if isinstance(info.get("days_remaining"), int):
            headline += f" ({info['days_remaining']} days)"
    else:
        headline = "No open comment periods or scheduled dates"
    verified = [str(item.get("verified_at", "")) for item in items if item.get("verified_at")]
    checked = ""
    if verified:
        try:
            checked_on = datetime.fromisoformat(max(verified)).astimezone(EASTERN).date()
            checked = (
                '<p class="note">Status comes from each proceeding&rsquo;s Federal Register record and OIRA review data, '
                f'not from news coverage. Last checked {_format_day(checked_on)}.</p>'
            )
        except ValueError:
            checked = ""
    rows = []
    for item in items:
        status = html.escape(str(item.get("status", "")))
        if item.get("oira_status"):
            status += f'<br><span class="oira">{html.escape(str(item["oira_status"]))}</span>'
        nxt = next_info(item)
        if nxt:
            label = html.escape(nxt["label"])
            remaining = (
                f'<br><span class="days">{nxt["days_remaining"]} days</span>'
                if isinstance(nxt.get("days_remaining"), int) and nxt["days_remaining"] >= 0 else ""
            )
            next_cell = label + remaining
        else:
            next_cell = "&mdash;"
        rule = html.escape(str(item.get("action", "")))
        key = str(item.get("rin") or item.get("docket") or "")
        citation = html.escape(str(item.get("citation", "")))
        link_text = f"{citation} (Federal Register)" if citation else "Official record"
        rows.append(
            "<tr>"
            f"<td><strong>{html.escape(str(item.get('agency', '')))}</strong><br>{rule}<br>"
            f'<span class="rin">{html.escape(key)}</span></td>'
            f"<td>{html.escape(str(item.get('fr_title', '')))}</td>"
            f"<td>{status}</td>"
            f"<td>{next_cell}</td>"
            f'<td><a href="{_safe_url(item.get("source_url", ""))}">{link_text}</a></td>'
            "</tr>"
        )
    return (
        '<details class="docket" id="tracker"><summary>'
        f"<h2>Regulatory docket</h2><span>{_plural(len(items), 'proceeding')} tracked &middot; {headline}</span></summary>"
        f"{checked}"
        '<table><thead><tr><th>Agency / Rule / RIN</th><th>Summary</th><th>Status</th><th>Next date</th><th>Official record</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></details>"
    )


def watch_html(items: list[Any]) -> str:
    entries = []
    for raw in items:
        text = " ".join(str(raw).split())
        if not text:
            continue
        match = _MONTH_DAY.match(text)
        when, what = (match.group(1), match.group(2)) if match else ("", text)
        entries.append(
            f"<li><time>{html.escape(when)}</time><span>{html.escape(what)}</span></li>"
        )
    if not entries:
        return ""
    return (
        '<section class="box" id="watch"><h2 class="label">What to watch</h2>'
        f'<ul class="watch">{"".join(entries)}</ul></section>'
    )


def market_html(snapshot: dict[str, Any] | None) -> str:
    """Compact market box: index tiles on top, grouped quote rows below. Hidden without a fresh snapshot."""
    if not snapshot:
        return ""

    def parts(quote: dict[str, Any]) -> tuple[str, str, str, str, str]:
        percent = float(quote["percent"])
        tone, arrow = ("up", "▲") if percent > 0 else ("down", "▼") if percent < 0 else ("flat", "–")
        name = html.escape(str(quote.get("name", quote["symbol"])), quote=True)
        return (
            html.escape(str(quote["symbol"])), name, f"{float(quote['price']):,.2f}", tone, f"{arrow} {abs(percent):.2f}%",
        )

    tiles, groups = [], {}
    for quote in snapshot["quotes"]:
        symbol, name, price, tone, change = parts(quote)
        if quote.get("group") == "Market overview":
            tiles.append(
                f'<li class="tile {tone}"><abbr title="{name}" tabindex="0">{symbol}</abbr>'
                f'<span class="px">{price}</span><span class="chg {tone}">{change}</span></li>'
            )
        else:
            groups.setdefault(str(quote.get("group", "Markets")), []).append(
                f'<li><abbr title="{name}" tabindex="0">{symbol}</abbr>'
                f'<span class="px">{price}</span><span class="chg {tone}">{change}</span></li>'
            )
    body = (f'<ul class="tiles">{"".join(tiles)}</ul>' if tiles else "") + "".join(
        f'<h3>{html.escape(group)}</h3><ul class="quotes">{"".join(rows)}</ul>' for group, rows in groups.items()
    )
    from market_watch import snapshot_label

    stamp = str(snapshot.get("session_label") or snapshot_label(snapshot))
    if not stamp:
        return ""  # no usable quote timestamps: hide the panel rather than guess a date
    stamp += "."
    return (
        '<section class="box market" id="market"><h2 class="label">Market watch</h2>'
        f'{body}<p class="note">{stamp} Delayed; not investment advice. Index tiles use the ETFs that track '
        "each index. Hover or tap a ticker for its full name.</p></section>"
    )


def _nav_links(payload: dict[str, Any]) -> list[tuple[str, str]]:
    links = [("summary", "Summary")]
    sections = payload.get("sections", {})
    for section in SECTION_ORDER:
        if sections.get(section):
            links.append((SECTION_ANCHORS[section], section_label(section)))
    links.append(("tracker", "Docket"))
    return links


def toc_html(payload: dict[str, Any]) -> str:
    """Wide screens: a quiet left column that stays in view and highlights the section being read."""
    anchors = "".join(
        f'<a href="#{target}" data-toc="{target}">{html.escape(label)}</a>' for target, label in _nav_links(payload)
    )
    return f'<nav class="toc no-copy" aria-label="Edition sections"><p class="toc-title">In this edition</p>{anchors}</nav>'


def section_nav_html(payload: dict[str, Any]) -> str:
    """Narrower screens: a collapsible menu (no sideways scrolling). Wide screens use the left column."""
    anchors = "".join(f'<a href="#{target}">{html.escape(label)}</a>' for target, label in _nav_links(payload))
    return (
        '<div class="sections-bar no-copy"><div class="wrap">'
        '<details class="menu"><summary>Jump to a section</summary>'
        f'<nav class="menu-grid" aria-label="Edition sections">{anchors}</nav></details>'
        "</div></div>"
    )


def hero_html(payload: dict[str, Any], day: date, *, eyebrow: str) -> str:
    lead = lead_story(payload)
    stories = ordered_stories(payload)
    sector_count = len({section for section, items in _glance_groups(payload.get("sections", {})) if items})
    window = _format_time(payload.get("window_end", ""))
    facts = [
        f"<li><b>{len(stories)}</b> stories</li>",
        f"<li><b>{sector_count}</b> sectors</li>",
    ]
    tracker = payload.get("regulatory_tracker", [])
    if tracker:
        facts.append(f"<li><b>{len(tracker)}</b> rulemakings tracked</li>")
    if window:
        facts.append(f"<li>Coverage through {html.escape(window)}</li>")
    if lead is None:
        headline = html.escape(_format_day(day))
        deck = ""
    else:
        headline = f'<a href="#{story_anchor(lead)}">{html.escape(display_title(lead))}</a>'
        text = sanitize_story_summary(str(lead.get("title", "")), str(lead.get("summary", "")))
        deck = f'<p class="deck">{rich(text)}</p>' if text else ""
    return (
        '<div class="wrap hero">'
        f'<p class="eyebrow">{html.escape(eyebrow)} &middot; {html.escape(_format_day(day))}</p>'
        f"<h1>{headline}</h1>{deck}"
        f'<ul class="facts">{"".join(facts)}</ul></div>'
    )


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
    badge = (
        ' &nbsp;<span style="font-family:Arial,Helvetica,sans-serif;font-size:10px;'
        'line-height:14px;color:#174F86;background:#EAF3FB;border:1px solid #AFCDE5;'
        'padding:2px 5px;border-radius:10px">Innovative Drone Use</span>'
        if use else ""
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
        <tr><td style="padding:0;font-family:Arial,Helvetica,sans-serif;font-size:16px;
            line-height:21px;font-weight:700;mso-line-height-rule:exactly">
          <strong><a href="{url}" style="color:#174F86;text-decoration:none;font-size:16px;line-height:21px;font-weight:700">{title}</a></strong>{badge}
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
    summary = summary_html(payload)
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
    for section, items in _glance_groups(sections):
        links = "".join(
            '<tr><td width="14" valign="top" style="width:14px;padding:1px 0 5px;'
            'font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:17px;color:#5C7182">'
            '&#8226;</td><td valign="top" style="padding:0 0 5px;font-family:Arial,Helvetica,sans-serif;'
            f'font-size:12px;line-height:17px"><a href="{_safe_url(item.get("url", ""))}" '
            'style="color:#294F6D;text-decoration:none">'
            f'{html.escape(str(item.get("title", "Untitled")))}</a></td></tr>'
            for item in items
        )
        headline_groups.append(
            '<tr><td style="padding:4px 0 3px;font-family:Arial,Helvetica,sans-serif;'
            'font-size:9px;line-height:13px;font-weight:bold;color:#73828D;letter-spacing:.25px">'
            f'{html.escape(section.upper())}</td></tr><tr><td style="padding:0 0 9px">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            f'style="width:100%;border-collapse:collapse">{links}</table></td></tr>'
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
    innovative_items = [
        item for items in sections.values() for item in items
        if clean_innovative_uas_use(str(item.get("innovative_uas_use", "")))
    ]
    innovative_markup = ""
    if innovative_items:
        links = "".join(
            '<tr><td style="padding:0 0 7px;font:13px/19px Arial,Helvetica,sans-serif">'
            f'<a href="{_safe_url(item.get("url", ""))}" style="color:#174F86;text-decoration:underline">'
            f'{html.escape(str(item.get("title", "")))}</a></td></tr>'
            for item in innovative_items
        )
        innovative_markup = (
            '<tr><td style="padding:0 28px 20px;font:18px/24px Arial,Helvetica,sans-serif;'
            'font-weight:700;color:#173C5E">Innovative Drone Uses in This Update</td></tr>'
            '<tr><td style="padding:0 28px 22px"><table role="presentation" width="100%">'
            + links + '</table></td></tr>'
        )
    section_markup = []
    for section in SECTION_ORDER:
        items = sections.get(section, [])
        if not items:
            continue
        heading_color = "#173C5E"
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
    watch_markup = ""
    deck = html.escape(lead_headline(payload))
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
              font-weight:bold;color:#FFFFFF">{html.escape(SITE_TITLE)}</div>
              <div style="padding-top:7px;font-size:14px;line-height:19px;color:#DCE8F0">
              {html.escape(_format_day(day))}</div>
              <div style="padding-top:9px;font-size:15px;line-height:21px;color:#FFFFFF">{deck}</div>
              </td></tr>{_email_spacer(18)}
          {summary_markup}{innovative_markup}{headlines_markup}{"".join(section_markup)}{tracker_markup}{watch_markup}
          <tr><td style="padding:0 28px 24px;font-family:Arial,Helvetica,sans-serif;font-size:11px;
              line-height:16px;color:#7B848C;border-top:1px solid #DFE5E9">
              This summary is AI generated.</td></tr>
        </table></td></tr></table></body></html>"""


def lead_headline(payload: dict[str, Any]) -> str:
    stories = [item for items in payload.get("sections", {}).values() for item in items]
    if not stories:
        return "Today's advanced transportation developments"
    return str(max(stories, key=lambda item: int(item.get("importance", 0) or 0)).get("title", ""))


def email_subject(payload: dict[str, Any], day: date) -> str:
    date_label = f"{day.month}/{day.day}/{day.year % 100:02d}"
    weekend = "Weekend + Monday — " if day.weekday() == 0 else ""
    return f"{SITE_TITLE} — {date_label} — {weekend}{lead_headline(payload)}"


def plain_text_email(payload: dict[str, Any], day: date) -> str:
    lines = [
        SITE_TITLE, _format_day(day), lead_headline(payload), "",
        "Executive Summary",
        sanitize_executive_summary_citations(
            str(payload.get("executive_summary", ""))
        ),
        "",
    ]
    sections = payload.get("sections", {})
    innovative = [
        item for items in sections.values() for item in items
        if item.get("innovative_uas_use")
    ]
    if innovative:
        lines.extend(["Innovative Drone Uses in This Update"])
        lines.extend(f"- {item.get('title', '')}: {item.get('url', '')}" for item in innovative)
        lines.append("")
    for section in SECTION_ORDER:
        items = sections.get(section, [])
        if not items:
            continue
        lines.extend([section, ""])
        for item in items:
            lines.extend([str(item.get("title", "")), str(item.get("url", ""))])
            if item.get("summary"):
                lines.append(str(item["summary"]))
            if item.get("also_covered"):
                lines.append("Additional coverage: " + " · ".join(
                    f"{related.get('source', '')} ({related.get('url', '')})"
                    for related in item["also_covered"]
                ))
            lines.append("")
    lines.extend(["Regulatory Tracker", ""])
    for item in payload.get("regulatory_tracker", []):
        days = item.get("days_remaining")
        deadline = (
            f"comment deadline {item.get('comment_deadline_label', '')}; "
            f"{days} days remaining"
            if isinstance(days, int)
            else f"comment period closed {item.get('comment_deadline_label', '')}"
        )
        lines.append(
            f"{item.get('agency', '')} — {item.get('action', '')} "
            f"({item.get('rin', '')}): {item.get('status', '')}; "
            f"{deadline}; "
            f"{item.get('source_url', '')}"
        )
    lines.extend(["", "This summary is AI generated."])
    return "\n".join(lines) + "\n"


def _outlook_tracker(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    rows = []
    for item in items:
        days = item.get("days_remaining")
        prefix = "Closes" if isinstance(days, int) else "Closed"
        values = (
            html.escape(str(item.get("agency", ""))) + " — "
            + html.escape(str(item.get("action", ""))) + " ("
            + html.escape(str(item.get("rin", ""))) + ")",
            html.escape(str(item.get("status", ""))),
            f'{prefix} {html.escape(str(item.get("comment_deadline_label", "")))}',
            str(days) if isinstance(days, int) else "—",
            f'<a href="{_safe_url(item.get("source_url", ""))}" style="color:#173C5E;'
            'text-decoration:underline">Official source</a>',
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
        for label in ("AGENCY / RULE (RIN)", "CURRENT STATUS", "DEADLINE / NEXT DATE", "DAYS", "SOURCE")
    )
    return (
        '<tr><td style="padding:0 28px"><table role="presentation" width="100%" cellspacing="0" '
        'cellpadding="0" style="width:100%;border-collapse:collapse"><tr><td style="padding:0 0 9px;'
        'font-family:Arial,Helvetica,sans-serif;font-size:22px;line-height:27px;font-weight:bold;'
        'color:#173C5E;border-bottom:2px solid #CBD6DE">Regulatory Tracker</td></tr>'
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
    """The copyable reading column: executive summary, sector stories, docket."""
    sections = payload.get("sections", {})
    if payload.get("edition_kind", "automated") == "editorial":
        intro = (
            '<section class="summary" id="summary"><h2 class="label">Executive summary</h2>'
            + "".join(f"<p>{paragraph}</p>" for paragraph in summary_paragraphs(payload))
            + eo_note(str(payload.get("executive_summary", "")))
            + "</section>"
        )
    else:
        shown = sum(len(items) for items in sections.values())
        intro = (
            '<section class="summary" id="summary"><h2 class="label">Today\'s headlines</h2>'
            f"<p>{_plural(shown, 'current headline')}, grouped by subject and checked against earlier editions.</p></section>"
        )

    section_markup = []
    for section in SECTION_ORDER:
        items = sections.get(section, [])
        if not items:
            continue
        section_markup.append(
            f'<section class="sector" id="{SECTION_ANCHORS[section]}">'
            f"<h2>{html.escape(section)}</h2>"
            + "".join(story_html(item) for item in items)
            + "</section>"
        )
    return intro + "".join(section_markup) + tracker_html(payload.get("regulatory_tracker", []))


FONT_LINKS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600'
    '&family=Hanken+Grotesk:wght@400;500;600&display=swap">'
)

CHART_SVG = (
    '<svg class="chart" viewBox="0 0 640 640" aria-hidden="true"><g fill="none" stroke="#3B5B8C" stroke-width="1" opacity=".55">'
    '<circle cx="400" cy="240" r="90"/><circle cx="400" cy="240" r="170"/><circle cx="400" cy="240" r="250"/>'
    '<circle cx="400" cy="240" r="330"/><path d="M400-100V580M60 240H740M160 0 640 480M640 0 160 480"/></g>'
    '<path d="M40 470C190 300 300 330 410 230S560 120 640 70" fill="none" stroke="#F2B33D" stroke-width="1.6" '
    'stroke-dasharray="3 7"/><circle cx="410" cy="230" r="5" fill="#F2B33D"/></svg>'
)


def page_shell(
    *,
    body: str,
    title: str,
    canonical_path: str,
    prefix: str = "",
    show_copy: bool = False,
    hero: str = "",
    active: str = "",
    edition_id: str = "",
    description: str = "Daily public-source advanced transportation headlines and a searchable archive of past editions.",
) -> str:
    copy_controls = ""
    if show_copy:
        copy_controls = (
            '<button type="button" data-copy-edition>Copy for email</button>'
            '<span class="copy-status" role="status" aria-live="polite" data-copy-status></span>'
        )

    def nav_link(key: str, label: str, href: str) -> str:
        current = ' aria-current="page"' if key == active else ""
        return f'<a href="{href}"{current}>{label}</a>'

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{html.escape(description, quote=True)}">
  <title>{html.escape(title)}</title>
  {f'<meta name="edition-id" content="{html.escape(edition_id, quote=True)}">' if edition_id else ''}
  <link rel="canonical" href="{SITE_URL}{canonical_path}">
  {FONT_LINKS}
  <link rel="stylesheet" href="{prefix}assets/site.css">
</head>
<body>
  <div class="cover no-copy">
    {CHART_SVG}
    <header class="wrap topbar">
      <a class="brand" href="{prefix or './'}" aria-label="{html.escape(SITE_TITLE)}">Advanced Transportation <i>Update</i></a>
      <nav aria-label="Primary navigation">
        {nav_link("latest", "Latest", prefix or "./")}
        {nav_link("archive", "Archive", prefix + "archive/")}
        {nav_link("search", "Search", prefix + "search/")}
        {copy_controls}
      </nav>
    </header>
    {hero}
  </div>
  {body}
  <footer class="footer no-copy"><div class="wrap"><span>This summary is AI generated.</span><span>{html.escape(SITE_TITLE)} &middot; news.peterjwhite.org</span></div></footer>
  <script src="{prefix}assets/site.js" defer></script>
</body>
</html>
"""


def side_html(payload: dict[str, Any], market: dict[str, Any] | None = None) -> str:
    watch = market_html(market)
    about = (
        '<p class="about">Compiled each weekday from public sources and checked against '
        "earlier editions. Browse the <a href=\"{archive}\">archive</a> or "
        "<a href=\"{search}\">search past headlines</a>.</p>"
    )
    return watch + about


def edition_page(payload: dict[str, Any], day: date, archived: bool, market: dict[str, Any] | None = None) -> str:
    prefix = _relative_prefix(archived)
    content = edition_content(payload, day)
    email_content = base64.b64encode(
        outlook_email_html(payload, day).encode("utf-8")
    ).decode("ascii")
    side = side_html(payload, market).replace("{archive}", prefix + "archive/").replace("{search}", prefix + "search/")
    body = f"""
      {section_nav_html(payload)}
      <main class="wrap page has-toc">
        {toc_html(payload)}
        <div class="reading newsletter" data-edition-content
            data-outlook-email-b64="{email_content}">{content}</div>
        <aside class="side no-copy" aria-label="Edition notes">{side}</aside>
      </main>
    """
    canonical = f"/archive/{day.isoformat()}/" if archived else "/"
    eyebrow = "Weekend and Monday briefing" if day.weekday() == 0 else "Daily briefing"
    return page_shell(
        body=body,
        title=f"{SITE_TITLE} — {_format_day(day)}",
        canonical_path=canonical,
        prefix=prefix,
        show_copy=True,
        hero=hero_html(payload, day, eyebrow=eyebrow),
        active="" if archived else "latest",
        edition_id=day.isoformat(),
    )


def archive_page(editions: dict[date, dict[str, Any]]) -> str:
    months: dict[tuple[int, int], list[str]] = {}
    for day in sorted(editions, reverse=True):
        payload = editions[day]
        count = len(ordered_stories(payload))
        lead = lead_story(payload)
        lead_text = html.escape(display_title(lead)) if lead else ""
        months.setdefault((day.year, day.month), []).append(
            f'<li><a href="{day.isoformat()}/"><strong>{html.escape(day.strftime("%a, %b %d").replace(" 0", " "))}</strong>'
            f'<span class="lead">{lead_text}</span><span class="count">{_plural(count, "story")}</span></a></li>'
        )
    groups = "".join(
        f'<h2>{html.escape(date(year, month, 1).strftime("%B %Y"))}</h2><ol>{"".join(rows)}</ol>'
        for (year, month), rows in months.items()
    )
    hero = (
        '<div class="wrap sub-hero"><p class="eyebrow">Past editions</p><h1>News archive</h1>'
        '<p>Open any date to read the full edition, or <a href="../search/" style="color:#F2B33D">search every headline</a>.</p></div>'
    )
    body = f'<main class="wrap archive">{groups}</main>'
    return page_shell(
        body=body,
        title=f"Archive — {SITE_TITLE}",
        canonical_path="/archive/",
        prefix="../",
        hero=hero,
        active="archive",
    )


def search_page(story_count: int, edition_count: int) -> str:
    hero = (
        '<div class="wrap sub-hero"><p class="eyebrow">Archive search</p><h1>Search past headlines</h1>'
        f'<p>Find stories across {edition_count:,} editions by headline, source, sector or year. '
        "Searches cover headlines only, not the text of the articles.</p></div>"
    )
    body = f"""
      <main class="wrap results">
        <form class="searchbox no-copy" data-search data-index="../search-index.json" role="search" aria-label="Search the archive">
          <input type="search" name="q" placeholder="Try &ldquo;Waymo&rdquo;, &ldquo;Part 108&rdquo; or &ldquo;counter-drone&rdquo;" aria-label="Search headlines" autocomplete="off">
          <select data-section aria-label="Sector"><option value="">All sectors</option></select>
          <select data-year aria-label="Year"><option value="">All years</option></select>
        </form>
        <p class="status" data-search-status role="status" aria-live="polite">Loading {story_count:,} headlines&hellip;</p>
        <div data-results></div>
        <p class="more" data-more hidden><button type="button">Show more</button></p>
      </main>
    """
    return page_shell(
        body=body,
        title=f"Search — {SITE_TITLE}",
        canonical_path="/search/",
        prefix="../",
        hero=hero,
        active="search",
        description="Search past Advanced Transportation Update headlines by keyword, sector and year.",
    )


def build_search_index(editions: dict[date, dict[str, Any]]) -> list[dict[str, str]]:
    """One row per published story: headline, source, sector, date and a link. No article text."""
    rows: list[dict[str, str]] = []
    for day in sorted(editions, reverse=True):
        for section, items in _glance_groups(editions[day].get("sections", {})):
            for item in items:
                row = {
                    "d": day.isoformat(),
                    "t": display_title(item).strip(),
                    "s": section_label(section),
                    "h": f"/archive/{day.isoformat()}/#{story_anchor(item)}",
                }
                source = canonical_source(str(item.get("source", "")))
                if source and source != "Source":
                    row["o"] = source
                if row["t"]:
                    rows.append(row)
    return rows


ASSET_DIR = Path(__file__).resolve().parent / "site_assets"
SITE_CSS = (ASSET_DIR / "site.css").read_text(encoding="utf-8")
SITE_JS = (ASSET_DIR / "site.js").read_text(encoding="utf-8")


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
        edition_page(
            editions[latest_day], latest_day, archived=False,
            market=load_market_snapshot(root / "data" / "market.json", today=latest_day),
        ),
        encoding="utf-8",
    )
    (output / "email.html").write_text(
        outlook_email_html(editions[latest_day], latest_day), encoding="utf-8"
    )
    (output / "email.txt").write_text(
        plain_text_email(editions[latest_day], latest_day), encoding="utf-8"
    )
    (output / "subject.txt").write_text(
        email_subject(editions[latest_day], latest_day) + "\n", encoding="utf-8"
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

    index_rows = build_search_index(editions)
    (output / "search-index.json").write_text(
        json.dumps(index_rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    (output / "search").mkdir()
    (output / "search" / "index.html").write_text(
        search_page(len(index_rows), len(editions)), encoding="utf-8"
    )
    (output / "assets" / "site.css").write_text(SITE_CSS, encoding="utf-8")
    (output / "assets" / "site.js").write_text(SITE_JS, encoding="utf-8")
    (output / "CNAME").write_text("news.peterjwhite.org\n", encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    return {"edition_count": len(editions), "latest_day": latest_day.toordinal(), "search_rows": len(index_rows)}


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
