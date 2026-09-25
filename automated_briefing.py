from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_providers import AIProvider, OpenAIProvider, provider_from_env
from coverage_history import load_published_history
from news_engine import (
    DEFAULT_OPENAI_MODEL,
    EASTERN,
    HEADLINE_PLACEHOLDER_PATTERN,
    SECTION_ORDER,
    TOPIC_SECTIONS,
    clean_innovative_uas_use,
    executive_summary_sentence_is_public,
    generate_briefing_from_records,
    headline_is_publisher_only,
    infer_innovative_uas_use,
    infer_section,
    sanitize_story_summary,
    story_summary_sentence_is_public,
)
from publication import briefing_date, briefing_payload
from supplemental_email import (
    extract_supplemental_items,
    headline_from_url_slug,
    headline_is_sentence_fragment,
)


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

    # Re-apply deterministic category rules to final story copy. This keeps
    # military/conflict and C-UAS stories out of generic UAS even when a model
    # or metadata-blocked supplemental item supplies a weak section label.
    moved: dict[str, list[dict[str, Any]]] = {
        section: [] for section in SECTION_ORDER
    }
    for display_section in SECTION_ORDER:
        for item in sections.get(display_section, []):
            title = str(item.get("title", ""))
            if headline_is_sentence_fragment(title):
                fallback_title = headline_from_url_slug(str(item.get("url", "")))
                if fallback_title:
                    item["title"] = title = fallback_title
            summary = sanitize_story_summary(
                title, str(item.get("summary", ""))
            )
            if headline_is_sentence_fragment(summary):
                summary = ""
            item["summary"] = summary
            target = display_section
            if display_section in TOPIC_SECTIONS:
                inferred = infer_section(item)
                # infer_section intentionally defaults ambiguous stories to UAS.
                # Only let that inference move records when the current section is
                # generic UAS, or when a stronger deterministic rule identifies
                # military, C-UAS, or AV-specific Federal coverage.
                should_move = (
                    inferred in {"Military UAS", "International Security", "Counter-UAS / Airspace Security"}
                    or (
                        display_section == "UAS / Drones"
                        and inferred != "UAS / Drones"
                    )
                    or (
                        display_section == "Federal Policy & Implementation"
                        and inferred == "Autonomous Vehicles"
                    )
                )
                if should_move:
                    target = inferred
                    item["section"] = inferred
            moved[target].append(item)
    briefing["sections"] = sections = moved

    for item in sections.get("UAS / Drones", []):
        item["innovative_uas_use"] = infer_innovative_uas_use(item)
    for section in SECTION_ORDER:
        if section == "UAS / Drones":
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
    raw_file: Path | None = None,
    api_key: str = "",
    model: str = DEFAULT_OPENAI_MODEL,
    supplemental_text: str = "",
    fetch_metadata: bool = True,
    provider: AIProvider | None = None,
) -> dict[str, Any]:
    selected = provider or (
        OpenAIProvider(api_key=api_key, model=model)
        if api_key.strip()
        else provider_from_env()
    )
    raw_feed = load_json(raw_file or root / "data" / "latest_raw_news.json")
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
        selected.model,
        previous_coverage=previous,
        provider=selected,
    )
    return normalize_reader_features(briefing)


def validate_complete_briefing(briefing: dict[str, Any]) -> dict[str, Any]:
    briefing = normalize_reader_features(briefing)
    errors: list[str] = []
    sections = briefing["sections"]
    if not executive_summary_sentence_is_public(
        str(briefing.get("executive_summary", ""))
    ):
        errors.append("The Executive Summary contains internal editorial language.")
    for section in SECTION_ORDER:
        for index, item in enumerate(sections.get(section, []), start=1):
            title = str(item.get("title", "")).strip()
            source = str(item.get("source", "")).strip()
            if HEADLINE_PLACEHOLDER_PATTERN.search(title):
                errors.append(f"{section} story {index} has a placeholder headline.")
            if headline_is_publisher_only(title, source):
                errors.append(f"{section} story {index} has a publisher-only headline.")
            if not str(item.get("url", "")).startswith(("http://", "https://")):
                errors.append(f"{section} story {index} has no public source URL.")
            summary = str(item.get("summary", "")).strip()
            if summary and not story_summary_sentence_is_public(summary):
                errors.append(f"{section} story {index} contains internal editorial language.")
            win_explanation = str(item.get("win_explanation", "")).strip()
            if win_explanation and not story_summary_sentence_is_public(win_explanation):
                errors.append(f"{section} story {index} has an internal Win explanation.")
    extracted = int(briefing.get("supplemental_count", 0) or 0)
    represented = int(briefing.get("supplemental_accounted_count", 0) or 0)
    if extracted != represented:
        errors.append(
            f"Supplemental accounting failed: extracted {extracted}, represented {represented}."
        )
    if not briefing.get("regulatory_tracker"):
        errors.append("The Regulatory Deadline Tracker is empty.")
    watch_items = briefing.get("what_to_watch")
    if not isinstance(watch_items, list) or not watch_items:
        errors.append("What to Watch is missing or empty.")
    elif any(not story_summary_sentence_is_public(str(item)) for item in watch_items):
        errors.append("What to Watch contains internal editorial language.")
    if errors:
        raise ValueError("Complete briefing validation failed:\n- " + "\n- ".join(errors))
    briefing["validation_status"] = "passed"
    return briefing


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
    parser.add_argument("--raw-file", type=Path, help="Use a dated raw snapshot for a test or backfill.")
    parser.add_argument("--skip-metadata-fetch", action="store_true")
    parser.add_argument("--health-check", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate-only", type=Path, metavar="BRIEFING_JSON")
    args = parser.parse_args()

    if args.validate_only:
        briefing = validate_complete_briefing(load_json(args.validate_only))
        print(
            json.dumps(
                {
                    "date": briefing_date(briefing),
                    "validation_status": briefing["validation_status"],
                    "supplemental_links_extracted": briefing.get("supplemental_count", 0),
                    "supplemental_links_represented": briefing.get(
                        "supplemental_accounted_count", 0
                    ),
                },
                indent=2,
            )
        )
        return

    provider = provider_from_env()
    health = provider.health_check()
    if args.health_check:
        print(json.dumps(health, indent=2))
        return

    supplemental_text = ""
    if args.supplemental_file:
        supplemental_text = args.supplemental_file.read_text(encoding="utf-8")
    elif args.event_path:
        supplemental_text = supplemental_text_from_event(args.event_path)

    root = args.root.resolve()
    started = time.monotonic()
    briefing = build_briefing(
        root,
        raw_file=args.raw_file,
        supplemental_text=supplemental_text,
        fetch_metadata=not args.skip_metadata_fetch,
        provider=provider,
    )
    briefing = validate_complete_briefing(briefing)
    if args.supplemental_file and int(briefing.get("supplemental_count", 0) or 0) == 0:
        raise ValueError(
            "The supplied email yielded no supplemental article links; "
            "refusing feed-only publication."
        )
    latest_path: Path | None = None
    archive_path: Path | None = None
    if not args.dry_run:
        latest_path, archive_path = save_briefing(root, briefing)
    story_count = sum(
        len(items) for items in briefing["sections"].values()
    )
    report = {
        "date": briefing_date(briefing),
        "ai_provider": briefing.get("ai_provider", provider.name),
        "model": briefing.get("model", provider.model),
        "raw_candidates_discovered": briefing.get("raw_automated_candidate_count", 0),
        "searxng_candidates": briefing.get("searxng_candidate_count", 0),
        "official_source_candidates": briefing.get("official_source_candidate_count", 0),
        "supplemental_links_extracted": briefing.get("supplemental_count", 0),
        "supplemental_links_represented": briefing.get(
            "supplemental_accounted_count", 0
        ),
        "final_story_count": story_count,
        "administration_wins_count": 0,
        "innovative_uas_uses_count": sum(
            bool(item.get("innovative_uas_use"))
            for item in briefing["sections"].get("UAS / Drones", [])
        ),
        "tracker_count": len(briefing.get("regulatory_tracker", [])),
        "model_retries": briefing.get("model_retries", 0),
        "total_build_seconds": round(time.monotonic() - started, 2),
        "validation_status": briefing.get("validation_status"),
        "saved": not args.dry_run,
    }
    print(json.dumps(report, indent=2))
    if latest_path and archive_path:
        print(
            f"Saved {latest_path.relative_to(root)} and "
            f"{archive_path.relative_to(root)}."
        )


if __name__ == "__main__":
    main()
