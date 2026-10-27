"""Regulatory tracker: append-only event ledger, fixed status rules, official-source verification.

Rebuild plan 7.2. Nothing here calls a model. Status is derived from the ledger of
Federal Register documents by fixed rules, so an outage or a changed API response
cannot silently change what an edition says.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests
import yaml

EASTERN = ZoneInfo("America/New_York")
TRACKER_DIR = Path(__file__).resolve().parent
WATCH_LIST = TRACKER_DIR / "rulemakings.yaml"
EVENTS_PATH = TRACKER_DIR / "events.jsonl"
VERIFICATIONS_PATH = TRACKER_DIR / "verifications.jsonl"
FR_DOCUMENTS_URL = "https://www.federalregister.gov/api/v1/documents.json"
FR_FIELDS = (
    "document_number",
    "type",
    "title",
    "publication_date",
    "html_url",
    "comments_close_on",
    "effective_on",
    "citation",
    "action",
    "dates",
    "docket_ids",
    "abstract",
)
OIRA_URL = "https://www.reginfo.gov/public/do/XMLViewFileAction?f=EO_RULES_UNDER_REVIEW.xml"
OIRA_PATH = TRACKER_DIR / "oira.json"
DISCOVERED_PATH = TRACKER_DIR / "discovered.yaml"
USER_AGENT = "AdvancedTransportationNewsBot/1.0 (+https://news.peterjwhite.org)"
# Daily Federal Register search: any agency, rules and proposed rules on these subjects.
DISCOVERY_TERMS = (
    "unmanned aircraft", "UAS", "drone", "counter-UAS", "automated driving systems", "supersonic",
    "powered-lift", "vertiport", "eVTOL", "advanced air mobility",
)
DISCOVERY_RELEVANCE = re.compile(
    r"unmanned aircraft|uas|drones?|counter-?uas|automated driving|ads|supersonic|powered.lift|vertiport|"
    r"evtol|advanced air mobility|beyond visual line", re.I)
DISCOVERY_LOOKBACK_DAYS = 3
# Federal Register document types that matter to rulemaking status.
RULEMAKING_TYPES = {"Rule", "Proposed Rule"}


def entry_key(entry: dict[str, Any]) -> str:
    """A RIN when the proceeding has one, else its id (docket-only proceedings such as an FCC docket)."""
    return str(entry.get("rin") or entry["id"])


def load_watch_list(path: Path = WATCH_LIST, *, include_discovered: bool = False) -> list[dict[str, Any]]:
    entries = list(yaml.safe_load(path.read_text(encoding="utf-8"))["rulemakings"])
    if include_discovered and path == WATCH_LIST and DISCOVERED_PATH.exists():
        extra = (yaml.safe_load(DISCOVERED_PATH.read_text(encoding="utf-8")) or {}).get("rulemakings") or []
        entries += [e for e in extra if entry_key(e) not in {entry_key(x) for x in entries}]
    seen: set[str] = set()
    for entry in entries:
        key = entry_key(entry)
        if key in seen:
            raise ValueError(f"Duplicate tracker key {key} in the watch list.")
        seen.add(key)
    return entries


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """Append-only: existing lines are never rewritten."""
    if not records:
        return
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def fetch_documents(
    rin: str, session: requests.Session | None = None, *, docket: str = "",
) -> list[dict[str, Any]]:
    """All Federal Register documents carrying this RIN (or docket), newest first. API only, never the HTML pages."""
    http = session or requests.Session()
    condition = {"conditions[docket_id]": docket} if docket else {"conditions[regulation_id_number]": rin}
    response = http.get(
        FR_DOCUMENTS_URL,
        params={**condition, "order": "newest", "per_page": 50, "fields[]": list(FR_FIELDS)},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()
    return list(response.json().get("results", []))


def new_events(
    rin: str,
    documents: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    observed_at: datetime,
) -> list[dict[str, Any]]:
    """Events not yet in the ledger. A document recorded before the ledger kept its action and citation is
    appended once more in the richer form (append-only: the old line is never edited)."""
    known = {(event["rin"], event["document_number"], "citation" in event) for event in existing}
    events = []
    for document in documents:
        key = (rin, document["document_number"], True)
        if key in known:
            continue
        known.add(key)
        events.append(
            {
                "rin": rin,
                "document_number": document["document_number"],
                "type": document.get("type", ""),
                "title": document.get("title", ""),
                "publication_date": document.get("publication_date", ""),
                "comments_close_on": document.get("comments_close_on") or "",
                "effective_on": document.get("effective_on") or "",
                "citation": document.get("citation") or "",
                "action": document.get("action") or "",
                "dates": document.get("dates") or "",
                "docket_ids": document.get("docket_ids") or [],
                "html_url": document.get("html_url", ""),
                "observed_at": observed_at.isoformat(),
            }
        )
    return events


def collapse_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One event per (key, document): the latest line wins, so enriched re-observations replace old ones."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        latest[(event["rin"], event["document_number"])] = event
    return list(latest.values())


_MONTHS = ("Jan.", "Feb.", "March", "April", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec.")


def _short(iso: str) -> str:
    day = date.fromisoformat(iso)
    return f"{_MONTHS[day.month - 1]} {day.day}, {day.year}"


def _label(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%B %d, %Y").replace(" 0", " ")


def classify_document(event: dict[str, Any]) -> str:
    """anprm | nprm | ifr | final | extension | correction | other, from the FR type and action text."""
    action = (event.get("action") or "").casefold()
    kind = event.get("type", "")
    if kind == "Rule":
        if "correct" in action:
            return "correction"
        return "ifr" if re.search(r"interim final|temporary final", action) else "final"
    if kind == "Proposed Rule":
        if "correct" in action and not re.search(r"proposed rulemaking \(nprm\)\.?$", action):
            return "correction"
        if re.search(r"advance notice|anprm", action):
            return "anprm"
        if re.search(r"extension|reopen|extend", action):
            return "extension"
        return "nprm"
    return "other"


_REPLY = re.compile(r"reply comments? (?:are |is )?(?:due )?(?:on or before )?([A-Z][a-z]+\.? \d{1,2}, \d{4})", re.I)


def _parse_long_date(text: str) -> str:
    try:
        return datetime.strptime(text.replace(".", ""), "%B %d, %Y").date().isoformat()
    except ValueError:
        try:
            return datetime.strptime(text.replace(".", ""), "%b %d, %Y").date().isoformat()
        except ValueError:
            return ""


def reply_deadline(event: dict[str, Any]) -> str:
    found = _REPLY.search(event.get("dates") or "")
    return _parse_long_date(found.group(1)) if found else ""


def derive_status(
    events: list[dict[str, Any]],
    as_of: date,
    *,
    oira: dict[str, Any] | None = None,
    meetings: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Status from the latest Federal Register document's type and action, plus OIRA review.

    ANPRM/SANPRM -> pre-proposal; NPRM -> proposed (with the latest comment close date, extensions included);
    interim final rule -> in effect; final rule -> published and effective. Prose is never consulted.
    """
    today = as_of.isoformat()
    docs = [e for e in collapse_events(events) if e["type"] in RULEMAKING_TYPES]
    docs.sort(key=lambda e: (e["publication_date"], e["document_number"]), reverse=True)
    published = [e for e in docs if e["publication_date"] <= today]
    upcoming = [e for e in docs if e["publication_date"] > today]
    stage_doc = next((e for e in published if classify_document(e) in {"final", "ifr", "nprm", "anprm"}), None)
    stage = classify_document(stage_doc) if stage_doc else ""

    result: dict[str, Any] = {
        "status": "", "stage": stage, "final_rule_document": "", "primary": stage_doc, "comment_deadline": "",
        "citation": stage_doc.get("citation", "") if stage_doc else "", "deadlines": [], "oira_status": "",
    }
    deadlines: list[dict[str, str]] = []
    if upcoming and classify_document(upcoming[0]) == "nprm" and (stage_doc is None or stage != "final"):
        nprm = upcoming[0]
        result.update(status=f"Proposed rule on public inspection; publishes {_label(nprm['publication_date'])}",
                      primary=nprm, comment_deadline=nprm.get("comments_close_on", ""), citation=nprm.get("citation", ""))
    elif stage == "final":
        eff = stage_doc.get("effective_on") or ""
        result.update(status=f"Final rule published {_short(stage_doc['publication_date'])}" + (f"; effective {_short(eff)}" if eff else ""),
                      final_rule_document=stage_doc["document_number"])
        if eff and eff >= today:
            deadlines.append({"label": "Takes effect", "date": eff, "kind": "deadline"})
    elif stage == "ifr":
        eff = stage_doc.get("effective_on") or stage_doc["publication_date"]
        close = max([e.get("comments_close_on") or "" for e in published if e["publication_date"] >= stage_doc["publication_date"]] or [""])
        text = f"Interim final rule {'in effect' if eff <= today else 'effective'} {_short(eff)}"
        if close:
            text += f"; comments {'close' if close >= today else 'closed'} {_short(close)}"
            result["comment_deadline"] = close
            if close >= today:
                deadlines.append({"label": "Comments due", "date": close, "kind": "deadline"})
        if eff > today:
            deadlines.append({"label": "Takes effect", "date": eff, "kind": "deadline"})
        result.update(status=text, final_rule_document=stage_doc["document_number"])
    elif stage in {"nprm", "anprm"}:
        since = [e for e in published if e["publication_date"] >= stage_doc["publication_date"]]
        close = max([e.get("comments_close_on") or "" for e in since] or [""])
        if stage == "anprm":
            kind = "SANPRM" if "supplemental" in (stage_doc.get("action") or "").casefold() or "supplemental" in (stage_doc.get("title") or "").casefold() else "ANPRM"
            result["status"] = f"Pre-proposal ({kind}, {_short(stage_doc['publication_date'])})"
        else:
            result["status"] = f"Proposed rule ({_short(stage_doc['publication_date'])})"
            if close:
                result["status"] += f"; comments {'close' if close >= today else 'closed'} {_short(close)}"
        if close:
            result["comment_deadline"] = close
            if close >= today:
                deadlines.append({"label": "Comments due", "date": close, "kind": "deadline"})
        reply = max([reply_deadline(e) for e in since] or [""])
        if reply and reply >= today:
            deadlines.append({"label": "Reply comments due", "date": reply, "kind": "deadline"})
    else:
        result["status"] = "No proposed or final rule in the Federal Register yet"

    if oira and oira.get("under_review") and oira.get("received"):
        result["oira_status"] = f"Under OIRA review since {_short(oira['received'])}"
        result["oira_stage"] = oira.get("stage", "")
    for meeting in sorted(set(meetings)):
        if meeting >= today:
            deadlines.append({"label": "EO 12866 meeting", "date": meeting, "kind": "meeting"})
    result["deadlines"] = sorted(deadlines, key=lambda d: d["date"])
    return result


def last_verifications(path: Path = VERIFICATIONS_PATH) -> dict[str, dict[str, Any]]:
    """Latest successful verification per RIN."""
    latest: dict[str, dict[str, Any]] = {}
    for record in read_jsonl(path):
        if record.get("ok"):
            latest[record["rin"]] = record
    return latest


def load_oira(path: Path = OIRA_PATH) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("rins", {})
    except (OSError, ValueError, AttributeError):
        return {}


def parse_oira_xml(xml_text: str, rins: set[str]) -> dict[str, dict[str, Any]]:
    """Tracked RINs found in reginfo.gov's EO 12866 'rules under review' file."""
    import xml.etree.ElementTree as ET

    found: dict[str, dict[str, Any]] = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return found
    for act in root.iter("REGACT"):
        rin = (act.findtext("RIN") or "").strip()
        if rin in rins:
            found[rin] = {
                "under_review": True,
                "received": (act.findtext("DATE_RECEIVED") or "").strip(),
                "stage": (act.findtext("STAGE") or "").strip(),
            }
    return found


def fetch_oira(session: requests.Session | None = None) -> str | None:
    """The OIRA review file, or None on any failure (OIRA status is then simply not shown)."""
    try:
        response = (session or requests).get(OIRA_URL, headers={"User-Agent": USER_AGENT}, timeout=40)
        if response.status_code != 200 or "REGACT" not in response.text:
            return None
        return response.text
    except requests.RequestException:
        return None


def build_tracker(
    as_of: date | datetime,
    *,
    events_path: Path = EVENTS_PATH,
    verifications_path: Path = VERIFICATIONS_PATH,
    watch_list: Path = WATCH_LIST,
    oira_path: Path = OIRA_PATH,
) -> list[dict[str, Any]]:
    """Deterministic, offline: read the watch list, ledger, OIRA snapshot and verification log."""
    as_of_date = as_of.date() if isinstance(as_of, datetime) else as_of
    events = read_jsonl(events_path)
    verifications = last_verifications(verifications_path)
    oira_all = load_oira(oira_path)
    items = []
    for entry in load_watch_list(watch_list, include_discovered=True):
        key = entry_key(entry)
        rin = str(entry.get("rin", ""))
        derived = derive_status(
            [e for e in events if e["rin"] == key],
            as_of_date,
            oira=oira_all.get(rin),
            meetings=entry.get("oira_meetings") or (),
        )
        primary = derived["primary"]
        deadline = derived["comment_deadline"]
        days = (date.fromisoformat(deadline) - as_of_date).days if deadline else None
        is_open = days is not None and days >= 0
        verification = verifications.get(key, {})
        upcoming = derived["deadlines"]
        nxt = upcoming[0] if upcoming else None
        item = {
            "id": entry["id"],
            "agency": entry["agency"],
            "rin": rin,
            "key": key,
            "action": entry["action"],
            "docket": ", ".join(primary["docket_ids"]) if primary else str(entry.get("docket", "")),
            "citation": derived["citation"],
            "fr_title": " ".join(str(primary.get("title", "")).split()) if primary else "",
            "stage": derived["stage"],
            "comment_deadline": deadline,
            "comment_deadline_label": _short(deadline) if deadline else "No concrete date published",
            "comment_period_closed_on": _short(deadline) if deadline and not is_open else "",
            "days_remaining": days if is_open else None,
            "post_comment_status": derived["status"],
            "status": derived["status"],
            "oira_status": derived["oira_status"],
            "next_date": (
                {"date": nxt["date"], "label": f"{_short(nxt['date'])} · {nxt['label']}", "kind": nxt["kind"],
                 "days_remaining": (date.fromisoformat(nxt["date"]) - as_of_date).days if nxt["kind"] == "deadline" else None}
                if nxt else None
            ),
            "upcoming": upcoming,
            "source_url": (
                primary["html_url"] if primary else entry.get("agenda_source", "")
            ),
            "final_rule_document": derived["final_rule_document"],
            "agenda_projection": (
                f"Estimate only (Unified Agenda): {entry['agenda_source']}" if entry.get("agenda_source") else ""
            ),
            "verified_at": verification.get("verified_at", ""),
        }
        items.append(item)
    return items


def discover(
    session: requests.Session, now: datetime, known_keys: set[str], known_dockets: set[str],
) -> list[dict[str, Any]]:
    """New rules and proposed rules on portfolio subjects from any agency, found with the FR API."""
    found: dict[str, dict[str, Any]] = {}
    since = (now.date() - timedelta(days=DISCOVERY_LOOKBACK_DAYS)).isoformat()
    for term in DISCOVERY_TERMS:
        try:
            response = session.get(
                FR_DOCUMENTS_URL,
                params={"conditions[term]": term, "conditions[type][]": ["RULE", "PRORULE"],
                        "conditions[publication_date][gte]": since, "order": "newest", "per_page": 20,
                        "fields[]": [*FR_FIELDS, "regulation_id_numbers", "agencies"]},
                headers={"User-Agent": USER_AGENT}, timeout=30,
            )
            response.raise_for_status()
            results = response.json().get("results", [])
        except (requests.RequestException, ValueError, AttributeError):
            continue
        for doc in results:
            text = f"{doc.get('title', '')} {doc.get('abstract', '') or ''}"
            if not DISCOVERY_RELEVANCE.search(text):
                continue
            rins = [r for r in doc.get("regulation_id_numbers") or [] if r]
            dockets = [d for d in doc.get("docket_ids") or [] if d]
            if not rins and not dockets:
                continue
            key = rins[0] if rins else re.sub(r"[^a-z0-9]+", "-", dockets[0].casefold()).strip("-")
            if key in known_keys or (not rins and dockets[0] in known_dockets):
                continue
            agencies = doc.get("agencies") or []
            found[key] = {
                "id": f"auto-{key.casefold()}",
                "agency": str((agencies[0] or {}).get("short_name") or (agencies[0] or {}).get("name") or "Agency") if agencies else "Agency",
                "action": " ".join(str(doc.get("title", "")).split())[:140],
                **({"rin": rins[0]} if rins else {"docket": dockets[0]}),
                "required": False,
                "auto_discovered": now.date().isoformat(),
            }
    return list(found.values())


def save_discovered(new_entries: list[dict[str, Any]], path: Path = DISCOVERED_PATH) -> None:
    if not new_entries:
        return
    current = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("rulemakings") if path.exists() else []
    merged = list(current or [])
    have = {entry_key(e) for e in merged}
    merged += [e for e in new_entries if entry_key(e) not in have]
    path.write_text(
        "# Written automatically by tracker.core.discover (daily Federal Register search). Safe to delete entries.\n"
        + yaml.safe_dump({"rulemakings": merged}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def refresh(
    *,
    now: datetime | None = None,
    session: requests.Session | None = None,
    status_check: Callable[[str], int | None] | None = None,
    events_path: Path = EVENTS_PATH,
    verifications_path: Path = VERIFICATIONS_PATH,
    watch_list: Path = WATCH_LIST,
    oira_fetch: Callable[[], str | None] | None = None,
    oira_path: Path = OIRA_PATH,
    discover_new: bool = True,
) -> dict[str, Any]:
    """Fetch every tracked proceeding, extend the ledger, read OIRA status, verify each official link.

    Returns a report. A fetch failure for one proceeding is recorded, never fatal, so the others still
    refresh; the gate reports stale verification.
    """
    from validation_gates import check_url_status, is_official_url

    check = status_check or check_url_status
    now = (now or datetime.now(EASTERN)).astimezone(EASTERN)
    http = session or requests.Session()
    existing = read_jsonl(events_path)
    report: dict[str, Any] = {"checked_at": now.isoformat(), "entries": [], "errors": [], "discovered": []}

    entries = load_watch_list(watch_list, include_discovered=watch_list == WATCH_LIST)
    if discover_new and watch_list == WATCH_LIST:
        known = {entry_key(e) for e in entries}
        dockets = {str(e.get("docket", "")) for e in entries if e.get("docket")}
        added = discover(http, now, known, dockets)
        if added:
            save_discovered(added)
            entries = load_watch_list(watch_list, include_discovered=True)
            report["discovered"] = [entry_key(e) for e in added]

    xml_text = (oira_fetch or (lambda: fetch_oira()))()
    rins = {str(e["rin"]) for e in entries if e.get("rin")}
    if xml_text:
        snapshot = parse_oira_xml(xml_text, rins)
        oira_path.write_text(json.dumps({"as_of": now.isoformat(), "rins": snapshot}, indent=1), encoding="utf-8")
    else:
        report["oira"] = "unavailable; status left as last saved"

    for entry in entries:
        key = entry_key(entry)
        try:
            documents = fetch_documents(str(entry.get("rin", "")), http, docket=str(entry.get("docket", "")) if not entry.get("rin") else "")
        except (requests.RequestException, ValueError) as exc:
            report["errors"].append(f"{key}: {exc}")
            report["entries"].append({"rin": key, "fetched": False})
            continue
        events = new_events(key, documents, existing, now)
        append_jsonl(events_path, events)
        existing.extend(events)
        item = next(
            i for i in build_tracker(now.date(), events_path=events_path,
                                     verifications_path=verifications_path,
                                     watch_list=watch_list, oira_path=oira_path)
            if i["key"] == key
        )
        url = item["source_url"]
        status = check(url) if is_official_url(url) else None
        ok = status == 200
        append_jsonl(
            verifications_path,
            [{"rin": key, "verified_at": now.isoformat(), "source_url": url,
              "http_status": status, "ok": ok}],
        )
        report["entries"].append(
            {"rin": key, "fetched": True, "new_events": len(events), "http_status": status,
             "status": item["status"]}
        )
        if not ok:
            report["errors"].append(f"{key}: official link {url} returned {status}")
    return report


if __name__ == "__main__":
    result = refresh()
    print(json.dumps(result, indent=2))
    raise SystemExit(1 if result["errors"] else 0)
