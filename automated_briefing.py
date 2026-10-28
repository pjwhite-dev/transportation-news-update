from __future__ import annotations

import argparse
import functools
import base64
import binascii
import json
import os
import time
from datetime import date, datetime, timedelta
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
    sanitize_executive_summary_citations,
    sanitize_story_summary,
    story_summary_sentence_is_public,
)
from publication import briefing_date, briefing_payload
from summary_chain import check_verifier, verifier_from_env
from tracker.core import refresh as refresh_tracker
from url_resolver import resolve_articles
from article_extract import enrich_candidates
from grounding import make_entailment_check, make_writer
from qa_report import QAReport
from quality_pass import QualityContext
from story_groups import build_store, write_store
from editorial_checks import scope_fits, style_text, strip_exclamations
from validation_gates import (
    check_url_status,
    drop_non_articles,
    edition_date,
    failing_checks,
    prune_unusable_stories,
    repair_story_failures,
    run_gates,
)
from supplemental_email import (
    extract_supplemental_items,
    headline_from_url_slug,
    headline_is_sentence_fragment,
)


MAX_SUPPLEMENTAL_EMAIL_BYTES = 2_000_000

# One live check per URL per run, shared by the prune step and the hard gates.
link_status = functools.lru_cache(maxsize=4096)(check_url_status)


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
                if should_move and scope_fits(item, inferred):
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

    summary = sanitize_executive_summary_citations(
        str(briefing.get("executive_summary", ""))
    )
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
    as_of: date | None = None,
    verifier: AIProvider | None = None,
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

    # Google News wrappers are not publisher links. Resolve them, and drop any
    # candidate that cannot be resolved rather than ever publishing a wrapper.
    link_stats = resolve_articles(
        raw_feed.get("articles", []), root / ".runtime" / "url_cache.json"
    )
    raw_feed["articles"] = [
        item for item in raw_feed.get("articles", []) if not item.get("url_unresolved")
    ]
    # Listing pages, social posts and SEO roundups are not stories; drop them before
    # the model spends tokens on them (the same filter runs again before the gates).
    raw_feed["articles"], dropped = drop_non_articles(raw_feed["articles"])
    link_stats["non_article_dropped"] = len(dropped)

    # Read each candidate's own page: real publication date, clean headline, article-ness, body text.
    qa = QAReport((as_of or end.date()).isoformat())
    qa.extend([{"stage": "non-article-url", "title": d["title"], "url": d["url"], "reason": "listing, social, quote, topic or roundup page"}
               for d in dropped])
    raw_feed["articles"], rejected = enrich_candidates(
        raw_feed["articles"], now=end, fetched_dir=root / ".runtime" / "fetched" / end.date().isoformat(),
        translate=make_translator(selected),
    )
    qa.extend(rejected)
    qa.note("candidates_after_article_check", len(raw_feed["articles"]))

    supplemental_records = extract_supplemental_items(
        supplemental_text,
        fetch_metadata=fetch_metadata,
    ) if supplemental_text.strip() else []
    previous = load_published_history(root / "data" / "archive", end.date())
    verifier = verifier or verifier_from_env()
    day = as_of or end.date()
    quality = QualityContext(
        report=qa, today=day, store=build_store(root / "data" / "archive", day),
        write=make_writer(selected), entail=make_entailment_check(verifier),
    )
    briefing = generate_briefing_from_records(
        raw_feed,
        supplemental_records,
        api_key,
        selected.model,
        previous_coverage=previous,
        provider=selected,
        quality=quality,
    )
    briefing["link_resolution"] = link_stats
    briefing["qa_report"] = qa.to_dict()
    briefing["quality_summary"] = qa.summary()
    return normalize_reader_features(briefing)


def make_translator(provider: AIProvider):
    """translate(headline) -> English headline, using the local writer model."""
    schema = {"type": "object", "properties": {"headline": {"type": "string"}}, "required": ["headline"]}

    def translate(headline: str) -> str:
        result = provider.generate_structured(
            [{"role": "system", "content": "Translate this news headline into clean, natural English. Keep names, numbers and places exactly. Return only the headline."},
             {"role": "user", "content": headline}],
            schema_name="headline_translation", schema=schema, max_output_tokens=120,
        )
        return str(result.value.get("headline", ""))

    return translate


def apply_house_style(briefing: dict[str, Any]) -> dict[str, Any]:
    """Money as '$43.6 million', executive orders by official title, no exclamation points in tags."""
    briefing["executive_summary"] = style_text(str(briefing.get("executive_summary", "")))
    for items in briefing.get("sections", {}).values():
        for story in items:
            story["summary"] = style_text(str(story.get("summary", "")))
            story["innovative_uas_use"] = strip_exclamations(str(story.get("innovative_uas_use", "")))
    return briefing


def validate_complete_briefing(
    briefing: dict[str, Any],
    *,
    today: date | None = None,
    check_links: bool = False,
    previous_cutoff: datetime | None = None,
) -> dict[str, Any]:
    """Run the editorial checks and the Section 7.3 hard gates; raise if any fail.

    The result carries ``validation_report`` (each check, its result and evidence).
    ``validation_status`` is kept only as a summary of that report.
    """
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
    report = run_gates(
        briefing,
        today=today,
        previous_cutoff=previous_cutoff,
        fetch_status=link_status if check_links else None,
        require_summary_chain=today is not None,
    )
    errors.extend(failing_checks(report))
    briefing["validation_report"] = report
    if errors:
        raise ValueError("Complete briefing validation failed:\n- " + "\n- ".join(errors))
    briefing["validation_status"] = "passed"
    return briefing


MINIMUM_STORIES_AFTER_REPAIR = 12
REPAIR_ROUNDS = 3


def repair_briefing(
    briefing: dict[str, Any],
    *,
    today: date | None,
    check_links: bool,
    rounds: int = REPAIR_ROUNDS,
    minimum_stories: int = MINIMUM_STORIES_AFTER_REPAIR,
) -> dict[str, Any]:
    """Run the gates, fix item-level failures, and run them again (a few rounds at most).

    The hard gates still decide publication afterwards; this only stops one bad story or link
    from failing an edition that is otherwise sound. A thin edition still fails.
    """
    briefing = normalize_reader_features(briefing)
    for _ in range(rounds):
        report = run_gates(
            briefing,
            today=today,
            fetch_status=link_status if check_links else None,
            require_summary_chain=today is not None,
        )
        removed = repair_story_failures(briefing, report, link_status if check_links else None)
        if not removed:
            break
        briefing.setdefault("pruned_items", []).extend(removed)
        print(f"Repair round removed or fixed {len(removed)} item(s): " + "; ".join(r["reason"] for r in removed[:5]))
    count = sum(len(items) for items in briefing["sections"].values())
    if count < minimum_stories:
        raise ValueError(f"Only {count} stories remain after repairs (minimum {minimum_stories}); refusing to publish a thin edition.")
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
    parser.add_argument("--as-of", type=date.fromisoformat, help="Treat this date as the edition date (replays and backfills).")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore today's saved draft and regenerate the whole briefing.",
    )
    parser.add_argument("--validate-only", type=Path, metavar="BRIEFING_JSON")
    parser.add_argument(
        "--require-today",
        action="store_true",
        help="With --validate-only: also require the edition date to be today in ET.",
    )
    parser.add_argument(
        "--skip-link-check",
        action="store_true",
        help="Skip live HTTP link checks (offline use only; never in production).",
    )
    args = parser.parse_args()

    if args.validate_only:
        briefing = validate_complete_briefing(
            load_json(args.validate_only),
            today=datetime.now(EASTERN).date() if args.require_today else None,
            check_links=not args.skip_link_check,
        )
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
    check_verifier(verifier_from_env())
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
    # Refresh the official-source ledger first; a source outage is recorded and the
    # tracker gate reports any verification that is stale or missing.
    tracker_refresh = refresh_tracker()
    today = args.as_of or datetime.now(EASTERN).date()
    draft_path = root / ".runtime" / f"draft-{today.isoformat()}.json"
    briefing = None
    if not (args.fresh or args.dry_run or args.raw_file) and draft_path.exists():
        # An earlier run today finished generating but failed validation or publishing. The expensive
        # part (analysis and the verified summary) is reused; links and gates are re-checked fresh.
        try:
            candidate = json.loads(draft_path.read_text(encoding="utf-8"))
            if edition_date(candidate) == today and candidate.get("summary_chain"):
                briefing = candidate
                print(f"Resuming from today's saved draft ({draft_path.name}); skipping generation.")
        except (OSError, ValueError):
            briefing = None
    if briefing is None:
        briefing = build_briefing(
            root,
            raw_file=args.raw_file,
            supplemental_text=supplemental_text,
            fetch_metadata=not args.skip_metadata_fetch,
            provider=provider,
            as_of=today,
        )
        if not args.dry_run and not args.raw_file:
            draft_path.parent.mkdir(exist_ok=True)
            draft_path.write_text(json.dumps(briefing, indent=1, ensure_ascii=False), encoding="utf-8")
    if args.dry_run:
        # Keep the raw model output so gate/prune changes can be tested without regenerating.
        scratch = root / ".runtime"
        scratch.mkdir(exist_ok=True)
        (scratch / "last_dry_run_unvalidated.json").write_text(
            json.dumps(briefing, indent=1, ensure_ascii=False), encoding="utf-8"
        )
    # Deterministic cleanup first (dead links, headline-echo summaries); every removal is
    # recorded in the edition, then the hard gates verify what remains.
    qa_snapshot = json.loads(json.dumps(briefing.get("qa_report") or {}))
    try:
        briefing["pruned_items"] = (
            [] if args.skip_link_check else prune_unusable_stories(briefing, link_status)
        )
        briefing = repair_briefing(briefing, today=today, check_links=not args.skip_link_check)
        # Final style pass on reader-facing prose (money, executive-order titles), then the QA report.
        briefing = apply_house_style(briefing)
        briefing = validate_complete_briefing(
            briefing,
            today=today,
            check_links=not args.skip_link_check,
        )
    except ValueError as error:
        # A refused edition still leaves its QA report, so the reason for every drop is on disk.
        if qa_snapshot.get("events") is not None:
            qa_snapshot["events"].append({"stage": "edition-refused", "title": "", "url": "", "reason": str(error)[:300]})
            failed_dir = (root / ".runtime" / "dryrun-qa") if args.dry_run else root / ".runtime" / "failed-qa"
            failed_dir.mkdir(parents=True, exist_ok=True)
            QAReport.from_dict(qa_snapshot).write(failed_dir)
        raise
    if args.supplemental_file and int(briefing.get("supplemental_count", 0) or 0) == 0:
        raise ValueError(
            "The supplied email yielded no supplemental article links; "
            "refusing feed-only publication."
        )
    latest_path: Path | None = None
    archive_path: Path | None = None
    qa_data = briefing.pop("qa_report", None)
    if qa_data:
        for item in briefing.get("pruned_items", []):
            qa_data["events"].append({"stage": "link-gate", "title": item.get("title", ""), "url": item.get("url", ""),
                                      "reason": item.get("reason", "")})
        qa_dir = (root / ".runtime" / "dryrun-qa") if args.dry_run else root
        qa_dir.mkdir(parents=True, exist_ok=True)
        QAReport.from_dict(qa_data).write(qa_dir)
    if args.dry_run:
        # The finished, validated edition, for before/after comparison with what was published.
        scratch = root / ".runtime"
        scratch.mkdir(exist_ok=True)
        (scratch / f"dryrun-{today.isoformat()}.json").write_text(
            json.dumps(briefing, indent=1, ensure_ascii=False), encoding="utf-8"
        )
    if not args.dry_run:
        latest_path, archive_path = save_briefing(root, briefing)
        write_store(root, build_store(root / "data" / "archive", today + timedelta(days=1)), today)
        draft_path.unlink(missing_ok=True)
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
        "pruned_items": len(briefing.get("pruned_items", [])),
        "tracker_refresh_errors": tracker_refresh["errors"],
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
