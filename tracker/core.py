"""Regulatory tracker: append-only event ledger, fixed status rules, official-source verification.

Rebuild plan 7.2. Nothing here calls a model. Status is derived from the ledger of
Federal Register documents by fixed rules, so an outage or a changed API response
cannot silently change what an edition says.
"""

from __future__ import annotations

import json
from datetime import date, datetime
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
    "docket_ids",
    "abstract",
)
# Federal Register document types that matter to rulemaking status.
RULEMAKING_TYPES = {"Rule", "Proposed Rule"}


def load_watch_list(path: Path = WATCH_LIST) -> list[dict[str, Any]]:
    entries = yaml.safe_load(path.read_text(encoding="utf-8"))["rulemakings"]
    seen: set[str] = set()
    for entry in entries:
        rin = entry["rin"]
        if rin in seen:
            raise ValueError(f"Duplicate RIN {rin} in the watch list.")
        seen.add(rin)
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


def fetch_documents(rin: str, session: requests.Session | None = None) -> list[dict[str, Any]]:
    """All Federal Register documents carrying this RIN, newest first."""
    http = session or requests.Session()
    response = http.get(
        FR_DOCUMENTS_URL,
        params={
            "conditions[regulation_id_number]": rin,
            "order": "newest",
            "per_page": 50,
            "fields[]": list(FR_FIELDS),
        },
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
    known = {(event["rin"], event["document_number"]) for event in existing}
    events = []
    for document in documents:
        key = (rin, document["document_number"])
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
                "docket_ids": document.get("docket_ids") or [],
                "html_url": document.get("html_url", ""),
                "observed_at": observed_at.isoformat(),
            }
        )
    return events


def derive_status(
    events: list[dict[str, Any]],
    as_of: date,
) -> dict[str, Any]:
    """Apply the fixed status rules to the RIN's Federal Register events."""
    rulemaking = [e for e in events if e["type"] in RULEMAKING_TYPES]
    rulemaking.sort(key=lambda e: (e["publication_date"], e["document_number"]), reverse=True)
    published = [e for e in rulemaking if e["publication_date"] <= as_of.isoformat()]
    upcoming = [e for e in rulemaking if e["publication_date"] > as_of.isoformat()]

    final_rules = [e for e in published if e["type"] == "Rule"]
    if final_rules:
        rule = final_rules[0]
        return {
            "status": f"Final rule published {_label(rule['publication_date'])}",
            "final_rule_document": rule["document_number"],
            "primary": rule,
            "comment_deadline": "",
        }
    proposals = [e for e in published + upcoming if e["type"] == "Proposed Rule"]
    if upcoming and upcoming[0]["type"] == "Proposed Rule":
        nprm = upcoming[0]
        return {
            "status": (
                f"Proposed rule on public inspection; publishes {_label(nprm['publication_date'])}"
            ),
            "final_rule_document": "",
            "primary": nprm,
            "comment_deadline": nprm["comments_close_on"],
        }
    with_deadline = [e for e in proposals if e["comments_close_on"]]
    if with_deadline:
        latest = with_deadline[0]
        if latest["comments_close_on"] >= as_of.isoformat():
            return {
                "status": "Open for comment",
                "final_rule_document": "",
                "primary": latest,
                "comment_deadline": latest["comments_close_on"],
            }
        return {
            "status": "Comment period closed; pending final rule",
            "final_rule_document": "",
            "primary": latest,
            "comment_deadline": latest["comments_close_on"],
        }
    if proposals:
        return {
            "status": "Proposed; no comment deadline published",
            "final_rule_document": "",
            "primary": proposals[0],
            "comment_deadline": "",
        }
    return {
        "status": "No proposed or final rule in the Federal Register yet",
        "final_rule_document": "",
        "primary": None,
        "comment_deadline": "",
    }


def _label(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%B %d, %Y").replace(" 0", " ")


def last_verifications(path: Path = VERIFICATIONS_PATH) -> dict[str, dict[str, Any]]:
    """Latest successful verification per RIN."""
    latest: dict[str, dict[str, Any]] = {}
    for record in read_jsonl(path):
        if record.get("ok"):
            latest[record["rin"]] = record
    return latest


def build_tracker(
    as_of: date | datetime,
    *,
    events_path: Path = EVENTS_PATH,
    verifications_path: Path = VERIFICATIONS_PATH,
    watch_list: Path = WATCH_LIST,
) -> list[dict[str, Any]]:
    """Deterministic, offline: read the watch list, ledger and verification log."""
    as_of_date = as_of.date() if isinstance(as_of, datetime) else as_of
    events = read_jsonl(events_path)
    verifications = last_verifications(verifications_path)
    items = []
    for entry in load_watch_list(watch_list):
        rin = entry["rin"]
        derived = derive_status(
            [e for e in events if e["rin"] == rin],
            as_of_date,
        )
        primary = derived["primary"]
        deadline = derived["comment_deadline"]
        days = (date.fromisoformat(deadline) - as_of_date).days if deadline else None
        is_open = days is not None and days >= 0
        verification = verifications.get(rin, {})
        item = {
            "id": entry["id"],
            "agency": entry["agency"],
            "rin": rin,
            "action": entry["action"],
            "docket": ", ".join(primary["docket_ids"]) if primary else "",
            "comment_deadline": deadline,
            "comment_deadline_label": _label(deadline) if deadline else "No concrete date published",
            "comment_period_closed_on": _label(deadline) if deadline and not is_open else "",
            "days_remaining": days if is_open else None,
            "post_comment_status": derived["status"],
            "status": derived["status"],
            "source_url": (
                primary["html_url"] if primary else entry["agenda_source"]
            ),
            "final_rule_document": derived["final_rule_document"],
            "agenda_projection": (
                f"Agency projection (Unified Agenda): {entry['agenda_source']}"
            ),
            "verified_at": verification.get("verified_at", ""),
        }
        items.append(item)
    return items


def refresh(
    *,
    now: datetime | None = None,
    session: requests.Session | None = None,
    status_check: Callable[[str], int | None] | None = None,
    events_path: Path = EVENTS_PATH,
    verifications_path: Path = VERIFICATIONS_PATH,
    watch_list: Path = WATCH_LIST,
) -> dict[str, Any]:
    """Fetch every required RIN, extend the ledger, verify each official link.

    Returns a report. A fetch failure for one RIN is recorded, never fatal, so the
    other entries still refresh; the gate reports stale verification.
    """
    from validation_gates import check_url_status, is_official_url

    check = status_check or check_url_status
    now = (now or datetime.now(EASTERN)).astimezone(EASTERN)
    http = session or requests.Session()
    existing = read_jsonl(events_path)
    report: dict[str, Any] = {"checked_at": now.isoformat(), "entries": [], "errors": []}
    for entry in load_watch_list(watch_list):
        rin = entry["rin"]
        try:
            documents = fetch_documents(rin, http)
        except (requests.RequestException, ValueError) as exc:
            report["errors"].append(f"{rin}: {exc}")
            report["entries"].append({"rin": rin, "fetched": False})
            continue
        events = new_events(rin, documents, existing, now)
        append_jsonl(events_path, events)
        existing.extend(events)
        item = next(
            i for i in build_tracker(now.date(), events_path=events_path,
                                     verifications_path=verifications_path,
                                     watch_list=watch_list)
            if i["rin"] == rin
        )
        url = item["source_url"]
        status = check(url) if is_official_url(url) else None
        ok = status == 200
        append_jsonl(
            verifications_path,
            [{"rin": rin, "verified_at": now.isoformat(), "source_url": url,
              "http_status": status, "ok": ok}],
        )
        report["entries"].append(
            {"rin": rin, "fetched": True, "new_events": len(events), "http_status": status,
             "status": item["status"]}
        )
        if not ok:
            report["errors"].append(f"{rin}: official link {url} returned {status}")
    return report


if __name__ == "__main__":
    result = refresh()
    print(json.dumps(result, indent=2))
    raise SystemExit(1 if result["errors"] else 0)
