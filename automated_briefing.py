from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from coverage_history import load_published_history
from news_engine import (
    DEFAULT_OPENAI_MODEL,
    EASTERN,
    SECTION_ORDER,
    clean_innovative_uas_use,
    generate_briefing_from_records,
    infer_innovative_uas_use,
)
from publication import briefing_date, briefing_payload
from supplemental_email import extract_supplemental_items


MAX_SUPPLEMENTAL_EMAIL_BYTES = 2_000_000


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


def supplemental_text_from_event(path: Path | None) -> str:
    """Read an optional base64-encoded email body without interpolating it in YAML."""
    if path is None or not path.exists():
        return ""
    event = load_json(path)
    candidates = (
        event.get("client_payload", {}).get("supplemental_email_b64", ""),
        event.get("inputs", {}).get("supplemental_email_b64", ""),
    )
    encoded = next((str(value).strip() for value in candidates if value), "")
    if not encoded:
        return ""
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("The supplemental email payload is not valid base64.") from exc
    if len(decoded) > MAX_SUPPLEMENTAL_EMAIL_BYTES:
        raise ValueError("The supplemental email payload is too large.")
    try:
        return decoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("The supplemental email payload must be UTF-8 text.") from exc


def normalize_reader_features(briefing: dict[str, Any]) -> dict[str, Any]:
    """Guarantee the reader-facing fields used by the website are present."""
    sections = briefing.get("sections")
    if not isinstance(sections, dict):
        raise ValueError("The generated briefing has no section map.")
    for section in SECTION_ORDER:
        sections.setdefault(section, [])
        if not isinstance(sections[section], list):
            raise ValueError(f"The generated {section} section is invalid.")

    for item in sections.get("UAS and Drones", []):
        label = clean_innovative_uas_use(item.get("innovative_uas_use", ""))
        item["innovative_uas_use"] = label or infer_innovative_uas_use(item)
    for section in SECTION_ORDER:
        if section == "UAS and Drones":
            continue
        for item in sections.get(section, []):
            item["innovative_uas_use"] = ""

    summary = str(briefing.get("executive_summary", "")).strip()
    if not summary:
        raise ValueError("The generated briefing has no Executive Summary.")
    if not isinstance(briefing.get("regulatory_tracker"), list):
        raise ValueError("The generated briefing has no regulatory tracker.")
    if not isinstance(briefing.get("what_to_watch"), list):
        raise ValueError("The generated briefing has no What to Watch list.")

    briefing["executive_summary"] = summary
    briefing["edition_kind"] = "editorial"
    briefing["publication_mode"] = "automated-ai"
    return briefing


def build_briefing(
    root: Path,
    *,
    api_key: str,
    model: str,
    supplemental_text: str = "",
    fetch_metadata: bool = True,
) -> dict[str, Any]:
    if not api_key.strip():
        raise ValueError("OPENAI_API_KEY is not configured.")
    raw_feed = load_json(root / "data" / "latest_raw_news.json")
    try:
        end = datetime.fromisoformat(str(raw_feed["window_end"])).astimezone(EASTERN)
    except (KeyError, ValueError) as exc:
        raise ValueError("The latest raw feed has no valid coverage end time.") from exc

    supplemental_records = extract_supplemental_items(
        supplemental_text,
        fetch_metadata=fetch_metadata,
    ) if supplemental_text.strip() else []
    previous = load_published_history(root / "data" / "archive", end.date())
    briefing = generate_briefing_from_records(
        raw_feed,
        supplemental_records,
        api_key,
        model,
        previous_coverage=previous,
    )
    return normalize_reader_features(briefing)


def save_briefing(root: Path, briefing: dict[str, Any]) -> tuple[Path, Path]:
    data_dir = root / "data"
    archive_dir = data_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    latest_path = data_dir / "latest_briefing.json"
    archive_path = archive_dir / f"{briefing_date(briefing)}.json"
    payload = briefing_payload(briefing)

    for path in (latest_path, archive_path):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
    return latest_path, archive_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build and save the complete AI-assisted transportation briefing."
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--event-path", type=Path)
    parser.add_argument("--supplemental-file", type=Path)
    parser.add_argument("--skip-metadata-fetch", action="store_true")
    args = parser.parse_args()

    supplemental_text = ""
    if args.supplemental_file:
        supplemental_text = args.supplemental_file.read_text(encoding="utf-8")
    elif args.event_path:
        supplemental_text = supplemental_text_from_event(args.event_path)

    root = args.root.resolve()
    model = os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip()
    briefing = build_briefing(
        root,
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        model=model or DEFAULT_OPENAI_MODEL,
        supplemental_text=supplemental_text,
        fetch_metadata=not args.skip_metadata_fetch,
    )
    latest_path, archive_path = save_briefing(root, briefing)
    story_count = sum(
        len(items) for items in briefing["sections"].values()
    )
    print(
        f"Built complete edition {briefing_date(briefing)} with {story_count} "
        f"section placements and {briefing.get('supplemental_count', 0)} "
        "supplemental links."
    )
    print(f"Saved {latest_path.relative_to(root)} and {archive_path.relative_to(root)}.")


if __name__ == "__main__":
    main()
