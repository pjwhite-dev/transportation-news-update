"""Bounded, resumable daily build used by Hermes cron (no free-form web agent)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import ctypes
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = (
    Path.cwd().resolve()
    if (Path.cwd() / "news_engine.py").is_file()
    else Path(__file__).resolve().parents[1]
)
sys.path.insert(0, str(ROOT))

from ai_providers import OllamaProvider  # noqa: E402
from automated_briefing import normalize_reader_features, validate_complete_briefing  # noqa: E402
from news_engine import (  # noqa: E402
    SECTION_ORDER,
    automated_record_is_portfolio_relevant,
    infer_section,
    sanitize_story_summary,
)
from public_site import outlook_email_html, plain_text_email  # noqa: E402
from regulatory_tracker import build_regulatory_tracker  # noqa: E402
from scripts.source_fact_check import normalize_url, run as run_fact_check  # noqa: E402
from summary_quality import has_one_or_two_sentences, sentence_count  # noqa: E402

EASTERN = ZoneInfo("America/New_York")
QUEUE = ROOT / ".runtime" / "atu-monitor" / "queue.json"
LOCK = ROOT / ".runtime" / "atu-daily-build.lock"
STATE = ROOT / ".runtime" / "atu-automation"
MAX_CANDIDATES = 80


def _date(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=EASTERN) if parsed.tzinfo is None else parsed.astimezone(EASTERN)
    except (TypeError, ValueError):
        return None


def _published_urls(before: datetime) -> set[str]:
    urls: set[str] = set()
    for path in (ROOT / "data" / "archive").glob("*.json"):
        try:
            if datetime.fromisoformat(path.stem).date() >= before.date():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for stories in payload.get("sections", {}).values():
            if not isinstance(stories, list):
                continue
            urls.update(str(item.get("url", "")) for item in stories if isinstance(item, dict))
    return urls


def select_candidates(now: datetime) -> list[dict]:
    payload = json.loads(QUEUE.read_text(encoding="utf-8"))
    items = payload.get("items", [])
    cutoff = now - timedelta(days=6)
    prior = _published_urls(now)
    unique: dict[str, dict] = {}
    for raw in items:
        item = dict(raw)
        url = str(item.get("url", "")).strip()
        seen = _date(str(item.get("first_seen") or item.get("published") or ""))
        if not url.startswith(("http://", "https://")) or url in prior or not seen or seen < cutoff:
            continue
        if not automated_record_is_portfolio_relevant(item):
            continue
        current = unique.get(url)
        if current is None or int(item.get("monitor_priority", 0) or 0) > int(current.get("monitor_priority", 0) or 0):
            unique[url] = item
    ranked = sorted(
        unique.values(),
        key=lambda item: (
            int(item.get("monitor_priority", 0) or 0),
            str(item.get("first_seen") or item.get("published") or ""),
        ),
        reverse=True,
    )
    # Reserve space across source categories before filling by priority.
    selected: list[dict] = []
    per_section: dict[str, int] = {}
    for item in ranked:
        section = str(item.get("search_section", "Other"))
        if per_section.get(section, 0) < 8:
            selected.append(item)
            per_section[section] = per_section.get(section, 0) + 1
        if len(selected) >= MAX_CANDIDATES:
            break
    selected_urls = {item["url"] for item in selected}
    for item in ranked:
        if len(selected) >= MAX_CANDIDATES:
            break
        if item["url"] not in selected_urls:
            selected.append(item)
            selected_urls.add(item["url"])
    return selected


def resumable_candidates(day: str, now: datetime) -> list[dict]:
    """Keep one stable candidate set for all retries of a daily edition."""
    manifest = ROOT / "data" / "runs" / day / "candidate_manifest.json"
    if manifest.is_file():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            items = payload.get("items", [])
            if payload.get("date") == day and isinstance(items, list) and len(items) >= 30:
                return [dict(item) for item in items if isinstance(item, dict)]
        except (OSError, ValueError, TypeError):
            pass
    return select_candidates(now)


def _all_stories(briefing: dict) -> list[dict]:
    return [story for stories in briefing.get("sections", {}).values() if isinstance(stories, list) for story in stories]


def collapse_exact_duplicate_titles(briefing: dict) -> dict:
    """Keep the highest-ranked occurrence of an identical source headline."""
    seen: set[str] = set()
    sections: dict[str, list[dict]] = {}
    for name, stories in briefing.get("sections", {}).items():
        kept: list[dict] = []
        for story in stories if isinstance(stories, list) else []:
            key = re.sub(r"[^a-z0-9]+", " ", str(story.get("title", "")).casefold()).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            kept.append(story)
        sections[name] = kept
    briefing["sections"] = sections
    return briefing


def restore_headline_only_summaries(briefing: dict, candidates: list[dict]) -> dict:
    """Restore a verbatim headline sentence after generic reader cleanup de-duplicates it."""
    records = {normalize_url(str(item.get("url", ""))): item for item in candidates}
    for story in _all_stories(briefing):
        record = records.get(normalize_url(str(story.get("url", ""))), {})
        source_text = str(record.get("description") or record.get("summary") or "").strip()
        if not source_text:
            title = str(record.get("original_title") or record.get("title") or story.get("title", "")).strip()
            story["summary"] = "Source headline: " + title.rstrip(" .!?;:") + "."
            story["confidence"] = "headline-only"
    return briefing


def deterministic_briefing(now: datetime, candidates: list[dict]) -> dict:
    """Build the edition skeleton directly from collected source records."""
    stories: list[dict] = []
    for record in candidates:
        title = str(record.get("original_title") or record.get("title") or "").strip()
        url = str(record.get("url", "")).strip()
        if not title or not url.startswith(("http://", "https://")):
            continue
        stories.append(
            {
                "id": str(record.get("id") or url),
                "title": title,
                "summary": "Source-grounded summary pending independent Llama prose pass.",
                "source": str(record.get("source", "Source")),
                "url": url,
                "published": str(record.get("published", "")),
                "date_label": str(record.get("date_label", "")),
                "section": infer_section(record),
                "importance": int(record.get("monitor_priority", 1) or 1),
                "confidence": "source-evidence",
                "innovative_uas_use": "",
                "is_administration_win": False,
                "eo_number": "",
                "eo_name": "",
                "eo_section": "",
                "eo_section_summary": "",
                "win_explanation": "",
                "also_covered": [],
            }
        )
        if len(stories) == 40:
            break
    if len(stories) < 30:
        raise RuntimeError(f"Only {len(stories)} usable grounded stories were available.")
    sections = {name: [] for name in SECTION_ORDER}
    for story in stories:
        sections.setdefault(story["section"], []).append(story)
    top = stories[:6]
    first = "; ".join(item["title"] for item in top[:3])
    second = "; ".join(item["title"] for item in top[3:6])
    return {
        "generated_at": now.isoformat(),
        "window_start": (now - timedelta(days=6)).isoformat(),
        "window_end": now.isoformat(),
        "model": "deterministic-selection + llama3.3:70b-instruct-q4_K_M-prose",
        "ai_provider": "ollama",
        "prose_model": "llama3.3:70b-instruct-q4_K_M",
        "executive_summary": f"Today's Advanced Transportation News Update covers {first}. Additional developments include {second}.",
        "what_to_watch": [f"Watch for follow-up developments related to {item['title']}." for item in top[:3]],
        "regulatory_tracker": build_regulatory_tracker(now),
        "sections": sections,
        "source_errors": [],
        "candidate_count": len(candidates),
        "raw_automated_candidate_count": len(candidates),
        "automated_candidate_count": len(candidates),
        "automated_filtered_out_count": 0,
        "previous_coverage_candidate_count": 0,
        "supplemental_count": 0,
        "supplemental_accounted_count": 0,
        "candidate_counts": {"hermes_monitor": len(candidates)},
        "candidate_origin_counts": {"Hermes source monitor": len(candidates)},
        "included_counts": {name: len(items) for name, items in sections.items()},
        "recognized_administration_win_ids": [],
        "coverage_floor_sections": [],
    }


def normalize_story_count(briefing: dict, candidates: list[dict]) -> dict:
    stories = _all_stories(briefing)
    stories.sort(key=lambda item: int(item.get("importance", 0) or 0), reverse=True)
    selected = stories[:40]
    used = {str(item.get("url", "")) for item in selected}
    if len(selected) < 30:
        for record in candidates:
            if str(record.get("url", "")) in used:
                continue
            title = str(record.get("original_title") or record.get("title") or "").strip()
            source_text = str(record.get("description") or record.get("summary") or "").strip()
            if not source_text:
                continue
            summary = sanitize_story_summary(
                title,
                source_text,
            )
            if not title or not has_one_or_two_sentences(summary):
                continue
            selected.append(
                {
                    "id": str(record.get("id", "")),
                    "title": title,
                    "summary": summary,
                    "source": str(record.get("source", "Source")),
                    "url": str(record.get("url", "")),
                    "published": str(record.get("published", "")),
                    "date_label": str(record.get("date_label", "")),
                    "section": infer_section(record),
                    "importance": int(record.get("monitor_priority", 1) or 1),
                    "confidence": "source-evidence",
                    "innovative_uas_use": "",
                    "is_administration_win": False,
                    "eo_number": "",
                    "eo_name": "",
                    "eo_section": "",
                    "eo_section_summary": "",
                    "win_explanation": "",
                    "also_covered": [],
                }
            )
            used.add(str(record.get("url", "")))
            if len(selected) >= 30:
                break
    if len(selected) < 30:
        raise RuntimeError(f"Only {len(selected)} grounded stories were available; publication requires 30.")
    sections = {name: [] for name in briefing.get("sections", {})}
    for story in selected:
        sections.setdefault(str(story.get("section", "Other Advanced Transportation")), []).append(story)
    briefing["sections"] = sections
    top = selected[:6]
    first = "; ".join(item["title"] for item in top[:3])
    second = "; ".join(item["title"] for item in top[3:6])
    briefing["executive_summary"] = (
        f"Today's Advanced Transportation News Update covers {first}."
        + (f" Additional developments include {second}." if second else "")
    )
    briefing["what_to_watch"] = [
        f"Watch for follow-up developments related to {item['title']}." for item in top[:3]
    ]
    return briefing


def repair_unsupported_claims(briefing: dict, candidates: list[dict], report: dict) -> dict:
    """Replace rejected model prose with collected evidence; remove dead/untraceable items."""
    records = {normalize_url(str(item.get("url", ""))): item for item in candidates}
    rejected_ids = {
        str(item.get("id", ""))
        for item in report.get("verdicts", [])
        if not item.get("supported", False)
    }
    dead_urls = {
        normalize_url(str(item.get("url", "")))
        for item in report.get("url_results", [])
        if item.get("status") in {404, 410}
    }
    repaired: list[dict] = []
    for story in _all_stories(briefing):
        url = normalize_url(str(story.get("url", "")))
        record = records.get(url)
        if not record or url in dead_urls:
            continue
        if str(story.get("id", "")) in rejected_ids:
            title = str(record.get("original_title") or record.get("title") or "").strip()
            source_text = str(record.get("description") or record.get("summary") or "").strip()
            if not source_text:
                continue
            summary = sanitize_story_summary(
                title,
                source_text,
            )
            if not title or not has_one_or_two_sentences(summary):
                continue
            story.update(
                {
                    "title": title,
                    "summary": summary,
                    "innovative_uas_use": "",
                    "is_administration_win": False,
                    "eo_number": "",
                    "eo_name": "",
                    "eo_section": "",
                    "eo_section_summary": "",
                    "win_explanation": "",
                }
            )
        repaired.append(story)
    briefing["sections"] = {name: [] for name in briefing.get("sections", {})}
    for story in repaired:
        briefing["sections"].setdefault(
            str(story.get("section", "Other Advanced Transportation")), []
        ).append(story)
    return normalize_story_count(briefing, candidates)


def write_prose_with_llama(briefing: dict, candidates: list[dict]) -> dict:
    """Write only source-grounded reader prose with the selected Llama model."""
    records = {normalize_url(str(item.get("url", ""))): item for item in candidates}
    evidence: list[dict] = []
    for story in _all_stories(briefing):
        record = records[normalize_url(str(story.get("url", "")))]
        evidence.append(
            {
                "id": str(story.get("id", "")),
                "source_headline": str(record.get("original_title") or record.get("title") or ""),
                "source_text": str(record.get("description") or record.get("summary") or "")[:1800],
            }
        )
    schema = {
        "type": "object",
        "properties": {
            "stories": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "summary": {"type": "string"},
                    },
                    "required": ["id", "summary"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["stories"],
        "additionalProperties": False,
    }
    llama = OllamaProvider(
        model="llama3.3:70b-instruct-q4_K_M",
        num_ctx=32768,
        timeout_seconds=7200,
        max_attempts=2,
        temperature=0.0,
        num_predict=6000,
    )
    prompt = (
        "Write a concise one- or two-sentence newsroom summary for every supplied story. "
        "Use only facts explicitly stated in source_headline and source_text. Do not infer "
        "dates, numbers, actors, causes, decisions, significance, or outcomes. If evidence "
        "is sparse, restate only the supported fact. Return every id exactly once.\n\n"
        + json.dumps(evidence, ensure_ascii=False)
    )
    expected = {item["id"] for item in evidence}
    day = datetime.fromisoformat(str(briefing["window_end"])).date().isoformat()
    cache_path = ROOT / "data" / "runs" / day / "llama_prose.json"
    summaries: dict[str, str] = {}
    if cache_path.is_file():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        summaries = {str(item["id"]): str(item["summary"]).strip() for item in cached.get("stories", [])}
    if set(summaries) != expected:
        result = llama.generate_structured(
            [{"role": "user", "content": prompt}],
            schema_name="grounded_newsroom_prose",
            schema=schema,
            max_output_tokens=6000,
        )
        summaries = {str(item["id"]): str(item["summary"]).strip() for item in result.value["stories"]}
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(result.value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if set(summaries) != expected:
        raise RuntimeError("Llama prose pass did not return exactly one summary per story.")
    for story in _all_stories(briefing):
        title = str(story.get("title", ""))
        record = records[normalize_url(str(story.get("url", "")))]
        source_text = str(record.get("description") or record.get("summary") or "").strip()
        if source_text:
            summary = sanitize_story_summary(title, summaries[str(story.get("id", ""))])
        else:
            # With no body text, the collected source headline is the full evidence.
            # Repeat it verbatim as a one-sentence summary; never manufacture details.
            summary = title.rstrip(" .!?;:") + "."
        if sentence_count(summary) == 0:
            summary = summary.rstrip(" ,;:") + "."
        elif sentence_count(summary) > 2:
            protected = summary
            for abbreviation in ("U.S.", "U.K.", "D.C.", "e.g.", "i.e.", "No.", "Inc.", "Corp.", "Co.", "Ltd."):
                protected = protected.replace(abbreviation, abbreviation.replace(".", "\u2024"))
            ends = list(re.finditer(r"[.!?](?=(?:[\"'’”\)\]]*)?(?:\s|$))", protected))
            if len(ends) >= 2:
                summary = summary[: ends[1].end()].strip()
        if not has_one_or_two_sentences(summary):
            raise RuntimeError(f"Llama prose is not one or two sentences: {story.get('id')}")
        story["summary"] = summary
    briefing["prose_model"] = llama.model
    return briefing


def write_artifacts(day: str, briefing: dict, candidates: list[dict]) -> Path:
    run = ROOT / "data" / "runs" / day
    run.mkdir(parents=True, exist_ok=True)
    (run / "candidate_manifest.json").write_text(
        json.dumps({"date": day, "generated_at": datetime.now(EASTERN).isoformat(), "items": candidates}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (run / f"briefing_{day}.json").write_text(json.dumps(briefing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    date_value = datetime.fromisoformat(day).date()
    (run / f"edition_{day}.html").write_text(outlook_email_html(briefing, date_value), encoding="utf-8")
    (run / f"edition_{day}.txt").write_text(plain_text_email(briefing, date_value), encoding="utf-8")
    return run


def run_command(*args: str) -> None:
    completed = subprocess.run(args, cwd=ROOT, check=False)
    if completed.returncode:
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(args)}")


def _lock_owner_is_running() -> bool:
    pid_path = LOCK / "pid"
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
        if os.name == "nt":
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                return bool(ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == 259
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def main() -> int:
    now = datetime.now(EASTERN)
    day = now.date().isoformat()
    run = ROOT / "data" / "runs" / day
    readiness = run / "qc_report.md"
    fact_report = run / "fact_check_report.json"
    if readiness.is_file() and "Overall: PASS" in readiness.read_text(encoding="utf-8") and fact_report.is_file():
        fact = json.loads(fact_report.read_text(encoding="utf-8"))
        if fact.get("status") == "passed":
            print(json.dumps({"status": "already-ready", "date": day}))
            return 0
    if LOCK.exists():
        if _lock_owner_is_running():
            print(json.dumps({"status": "build-already-running", "date": day}))
            return 0
        shutil.rmtree(LOCK, ignore_errors=True)
    LOCK.mkdir(parents=True)
    (LOCK / "pid").write_text(str(os.getpid()), encoding="utf-8")
    STATE.mkdir(parents=True, exist_ok=True)
    try:
        os.environ["AI_PROVIDER"] = "ollama"
        # Qwen performs bounded structured sorting. Llama 3.3 separately writes
        # the reader-facing prose, then a fresh Llama call fact-checks it.
        os.environ["OLLAMA_MODEL"] = "qwen3.8:27b-q4_K_M"
        os.environ["OLLAMA_NUM_CTX"] = "65536"
        os.environ["OLLAMA_TIMEOUT_SECONDS"] = "1800"
        os.environ["OPENAI_FALLBACK_ENABLED"] = "false"
        candidates = resumable_candidates(day, now)
        if len(candidates) < 30:
            raise RuntimeError(f"Only {len(candidates)} unique grounded candidates were available.")
        briefing = deterministic_briefing(now, candidates)
        briefing = collapse_exact_duplicate_titles(write_prose_with_llama(briefing, candidates))
        briefing = restore_headline_only_summaries(normalize_reader_features(briefing), candidates)
        briefing = validate_complete_briefing(briefing)
        write_artifacts(day, briefing, candidates)
        report = run_fact_check(day)
        for _ in range(2):
            if report.get("status") == "passed":
                break
            briefing = repair_unsupported_claims(briefing, candidates, report)
            briefing = restore_headline_only_summaries(normalize_reader_features(briefing), candidates)
            briefing = validate_complete_briefing(briefing)
            write_artifacts(day, briefing, candidates)
            report = run_fact_check(day)
        if report.get("status") != "passed":
            raise RuntimeError("Independent fact-check gate remained blocked after automatic repair.")
        python = str(ROOT / ".venv" / "Scripts" / "python.exe")
        run_command(python, str(ROOT / "scripts" / "final_edition_qc.py"), "--date", day)
        run_command(python, str(ROOT / "scripts" / "final_summary_qc.py"), "--date", day)
        (STATE / "last_success.json").write_text(json.dumps({"date": day, "completed_at": datetime.now(EASTERN).isoformat()}, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "ready", "date": day, "stories": len(_all_stories(briefing))}))
        return 0
    except Exception as exc:
        failure = {"date": day, "failed_at": datetime.now(EASTERN).isoformat(), "error": str(exc)}
        (STATE / "last_failure.json").write_text(json.dumps(failure, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "failed", **failure}))
        return 1
    finally:
        shutil.rmtree(LOCK, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
