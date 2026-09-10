from __future__ import annotations

import argparse
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
    distinct_story_summary,
    infer_innovative_uas_use,
    infer_section,
)
from regulatory_tracker import build_regulatory_tracker


SITE_URL = "https://news.peterjwhite.org"
SITE_TITLE = "Advanced Transportation News Update"

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
        return parsed.strftime("%-I:%M %p ET on %B %d, %Y").replace(" 0", " ")
    except (TypeError, ValueError):
        return str(value or "")


def _relative_prefix(is_archive_page: bool) -> str:
    return "../../" if is_archive_page else ""


def story_html(item: dict[str, Any]) -> str:
    title = html.escape(str(item.get("title", "Untitled")))
    summary = html.escape(
        distinct_story_summary(
            str(item.get("title", "")), str(item.get("summary", ""))
        )
    )
    source = html.escape(str(item.get("source", "Source")))
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
            f'{html.escape(str(related_item.get("source", "Related coverage")))}</a>'
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
        f'<p class="highlight"><strong>Innovative UAS use:</strong> '
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
      <article class="story">
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
      <section class="newsletter-section tracker">
        <h2>Regulatory Deadline Tracker</h2>
        <div class="table-scroll"><table>
          <thead><tr><th>Agency</th><th>Action</th><th>Comment period</th><th>Days</th><th>Status</th></tr></thead>
          <tbody>""" + "".join(rows) + """</tbody>
        </table></div>
      </section>
    """


def headline_index_html(sections: dict[str, list[dict[str, Any]]]) -> str:
    groups = []
    for section in SECTION_ORDER:
        items = sections.get(section, [])
        if not items:
            continue
        links = "".join(
            f'<li><a href="{_safe_url(item.get("url", ""))}">'
            f'{html.escape(str(item.get("title", "Untitled")))}</a></li>'
            for item in items
        )
        groups.append(
            f'<div><h3>{html.escape(section)}</h3><ul>{links}</ul></div>'
        )
    if not groups:
        return ""
    return (
        '<section class="headline-index"><h2>Headlines at a Glance</h2>'
        + "".join(groups)
        + "</section>"
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
    return "".join(cards)


def edition_content(payload: dict[str, Any], day: date) -> str:
    sections = payload.get("sections", {})
    kind = payload.get("edition_kind", "automated")
    if kind == "editorial":
        intro = (
            '<section class="executive-summary"><h2>Executive Summary</h2>'
            f'<p>{html.escape(str(payload.get("executive_summary", "")))}</p></section>'
        )
        status = "Owner-published editorial edition"
    else:
        shown = sum(len(items) for items in sections.values())
        status = "Automatically updated public-source headline edition"
        intro = f"""
          <section class="automated-note">
            <strong>{shown} current headlines</strong>
            <span>Grouped by subject and checked against earlier editions.</span>
          </section>
        """

    section_markup = []
    for section in SECTION_ORDER:
        items = sections.get(section, [])
        if not items:
            continue
        section_markup.append(
            f'<section class="newsletter-section"><h2>{html.escape(section)}</h2>'
            + "".join(story_html(item) for item in items)
            + "</section>"
        )

    tracker = tracker_html(payload.get("regulatory_tracker", []))
    watch = payload.get("what_to_watch", [])
    watch_markup = ""
    if watch:
        watch_markup = (
            '<section class="newsletter-section"><h2>What to Watch</h2><ul class="watch">'
            + "".join(f"<li>{html.escape(str(item))}</li>" for item in watch)
            + "</ul></section>"
        )

    return f"""
      <div class="edition-heading">
        <p class="eyebrow">{html.escape(status)}</p>
        <h1>{SITE_TITLE}</h1>
        <p class="edition-date">{html.escape(_format_day(day))}</p>
        <p class="coverage">Coverage through {_format_time(payload.get('window_end', ''))}</p>
      </div>
      {intro}
      {top_highlights_html(payload)}
      {headline_index_html(sections)}
      {''.join(section_markup)}
      {tracker}
      {watch_markup}
      <footer>Public source, AI-assisted news update.</footer>
    """


def page_shell(
    *,
    body: str,
    title: str,
    canonical_path: str,
    prefix: str = "",
) -> str:
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
    <a class="brand" href="{prefix}">{SITE_TITLE}</a>
    <nav aria-label="Primary navigation">
      <a href="{prefix}">Latest</a>
      <a href="{prefix}archive/">Archive</a>
    </nav>
  </header>
  {body}
  <script src="{prefix}assets/site.js" defer></script>
</body>
</html>
"""


def edition_page(payload: dict[str, Any], day: date, archived: bool) -> str:
    prefix = _relative_prefix(archived)
    content = edition_content(payload, day)
    body = f"""
      <main>
        <div class="page-actions no-copy">
          <button type="button" data-copy-edition>Copy for email</button>
          <a href="{prefix}archive/">Browse past editions</a>
          <span role="status" aria-live="polite" data-copy-status></span>
        </div>
        <div class="newsletter" data-edition-content>{content}</div>
      </main>
    """
    canonical = f"/archive/{day.isoformat()}/" if archived else "/"
    return page_shell(
        body=body,
        title=f"{SITE_TITLE} — {_format_day(day)}",
        canonical_path=canonical,
        prefix=prefix,
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
:root{--navy:#153a5a;--ink:#1f2933;--muted:#647482;--line:#d9e2e8;--paper:#fff;--wash:#f2f6f8;--yellow:#fff2b8;--red:#8c241e;--max:800px;color-scheme:light}
*{box-sizing:border-box}
html{font-size:16px;background:#e9eef2}
body{margin:0;color:var(--ink);font-family:Arial,Helvetica,sans-serif;line-height:1.5}
a{color:#174f77}
.site-header{min-height:64px;padding:0 max(20px,calc((100vw - 1050px)/2));display:flex;align-items:center;justify-content:space-between;gap:24px;background:var(--navy);color:#fff}
.site-header a{color:#fff;text-decoration:none}.brand{font-weight:800;letter-spacing:-.01em}.site-header nav{display:flex;gap:22px;font-size:.92rem}
main{width:min(var(--max),calc(100% - 32px));margin:26px auto 60px}
.page-actions{display:flex;align-items:center;gap:16px;margin-bottom:14px;font-size:.9rem}.page-actions button{appearance:none;border:0;border-radius:5px;background:var(--navy);color:#fff;padding:10px 15px;font:inherit;font-weight:700;cursor:pointer}.page-actions button:hover{background:#0d2e49}.page-actions span{color:#32633a}
.newsletter{background:var(--paper);border:1px solid #dfe5e9;box-shadow:0 14px 40px rgba(26,51,70,.08);padding:42px 48px}
.edition-heading{border-bottom:4px solid var(--navy);padding-bottom:20px;margin-bottom:24px}.eyebrow{text-transform:uppercase;letter-spacing:.09em;font-size:.75rem;font-weight:800;color:#506c80;margin:0 0 7px}.edition-heading h1,.archive-heading h1{font-size:2.15rem;line-height:1.08;color:var(--navy);letter-spacing:-.025em;margin:0}.edition-date{font-size:1.06rem;font-weight:700;margin:10px 0 0}.coverage{font-size:.8rem;color:var(--muted);margin:3px 0 0}
.executive-summary{background:#eaf2f7;border-left:4px solid #4d7898;padding:16px 18px;margin:0 0 22px}.executive-summary h2,.headline-index>h2{font-size:.76rem;text-transform:uppercase;letter-spacing:.06em;color:#244d6b;margin:0 0 7px}.executive-summary p{margin:0}
.automated-note{display:flex;justify-content:space-between;gap:18px;background:var(--yellow);border-left:4px solid #d19900;padding:12px 14px;margin-bottom:22px;font-size:.9rem}.automated-note span{color:#665511}
.top-highlight{background:var(--yellow);border-left:4px solid #d19900;padding:11px 14px;margin:0 0 10px;font-size:.85rem;color:#4d420d}.top-highlight>strong{text-transform:uppercase;letter-spacing:.04em;font-size:.72rem}.top-highlight p,.top-highlight ul{margin:5px 0 0}.top-highlight ul{padding-left:20px}
.headline-index{border:1px solid var(--line);background:#fafcfd;padding:14px 16px 8px;margin-bottom:28px}.headline-index>div{margin:0 0 12px}.headline-index h3{font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;color:#597184;margin:0 0 4px}.headline-index ul{font-size:.82rem;line-height:1.45;margin:0;padding-left:20px}.headline-index li{margin:2px 0}.headline-index a{text-decoration:none}
.newsletter-section{margin:0 0 32px}.newsletter-section>h2{font-size:1.3rem;line-height:1.2;color:var(--navy);border-bottom:2px solid #cbd6de;padding-bottom:7px;margin:0 0 15px}.newsletter-section:first-of-type>h2{color:var(--red)}
.story{border-bottom:1px solid #e1e6ea;padding:0 0 18px;margin:0 0 18px}.story h3{font-size:1.08rem;line-height:1.32;margin:0 0 7px}.story h3 a{text-decoration:none;color:#173c5e}.story-summary{margin:0 0 7px}.meta,.related{font-size:.76rem;color:var(--muted);margin:5px 0 0}.highlight{background:var(--yellow);border-left:3px solid #d6b656;padding:7px 9px;font-size:.82rem}.win-callout{background:#fff1ed;border-left:3px solid #b42318;padding:10px 12px;margin:10px 0;font-size:.84rem;color:#57201b}.win-callout p{margin:4px 0 0}
.table-scroll{overflow-x:auto}.tracker table{border-collapse:collapse;width:100%;font-size:.78rem}.tracker th{text-align:left;background:var(--wash);color:#526574;text-transform:uppercase;font-size:.68rem}.tracker th,.tracker td{padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:top}.watch{margin:0;padding-left:22px}.watch li{margin-bottom:6px}footer{border-top:1px solid var(--line);padding-top:12px;color:#7b848c;font-size:.72rem}
.archive-page{width:min(760px,calc(100% - 32px))}.archive-heading{background:#fff;border:1px solid var(--line);padding:34px 38px;margin-bottom:16px}.archive-heading p:last-child{margin-bottom:0;color:var(--muted)}.archive-list{list-style:none;padding:0;margin:0;display:grid;gap:10px}.archive-list a{display:flex;justify-content:space-between;align-items:center;gap:20px;background:#fff;border:1px solid var(--line);padding:17px 20px;text-decoration:none;color:var(--navy)}.archive-list a:hover{border-color:#7892a4;box-shadow:0 5px 20px rgba(26,51,70,.07)}.archive-list span{font-size:.82rem;color:var(--muted);text-align:right}
@media(max-width:680px){.site-header{padding:0 18px}.brand{font-size:.9rem}.site-header nav{gap:14px}.newsletter{padding:28px 20px}.edition-heading h1,.archive-heading h1{font-size:1.75rem}.page-actions{align-items:flex-start;flex-wrap:wrap}.automated-note,.archive-list a{align-items:flex-start;flex-direction:column;gap:4px}.archive-list span{text-align:left}.tracker table{min-width:680px}.archive-heading{padding:28px 22px}}
@media print{html{background:#fff}.site-header,.no-copy{display:none!important}main{width:100%;margin:0}.newsletter{border:0;box-shadow:none;padding:0}}
""".strip() + "\n"


SITE_JS = r"""
(function () {
  const button = document.querySelector('[data-copy-edition]');
  const content = document.querySelector('[data-edition-content]');
  const status = document.querySelector('[data-copy-status]');
  if (!button || !content) return;

  button.addEventListener('click', async function () {
    const rich = '<div style="font-family:Arial,Helvetica,sans-serif;max-width:760px">' + content.innerHTML + '</div>';
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

    latest_day = max(editions)
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
