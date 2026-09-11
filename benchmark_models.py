from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from ai_providers import OllamaProvider
from automated_briefing import validate_complete_briefing
from news_engine import (
    HEADLINE_PLACEHOLDER_PATTERN,
    SECTION_ORDER,
    STORY_SUMMARY_PROCESS_PATTERN,
    executive_summary_sentence_is_public,
    generate_briefing_from_records,
)


class MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ulong),
        ("memory_load", ctypes.c_ulong),
        ("total_physical", ctypes.c_ulonglong),
        ("available_physical", ctypes.c_ulonglong),
        ("total_page_file", ctypes.c_ulonglong),
        ("available_page_file", ctypes.c_ulonglong),
        ("total_virtual", ctypes.c_ulonglong),
        ("available_virtual", ctypes.c_ulonglong),
        ("available_extended_virtual", ctypes.c_ulonglong),
    ]


def system_memory_used_mb() -> int | None:
    try:
        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return round((status.total_physical - status.available_physical) / 1024 / 1024)
    except (AttributeError, OSError):
        return None


def gpu_memory_used_mb() -> int | None:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return max(int(line.strip()) for line in result.stdout.splitlines() if line.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def archive_records(payload: dict[str, Any], maximum: int) -> tuple[list[dict[str, Any]], dict[str, str]]:
    records: list[dict[str, Any]] = []
    expected: dict[str, str] = {}
    seen: set[str] = set()
    for section in SECTION_ORDER:
        if section in {"Trump Administration Wins", "Top Developments"}:
            continue
        for item in payload.get("sections", {}).get(section, []):
            url = str(item.get("url", "")).strip()
            title = str(item.get("title", "")).strip()
            if not url or not title or url in seen:
                continue
            seen.add(url)
            expected[url] = section
            records.append(
                {
                    "id": f"benchmark-{len(records) + 1}",
                    "title": title,
                    "original_title": title,
                    "summary": str(item.get("summary", "")),
                    "source": str(item.get("source", "")),
                    "url": url,
                    "published": payload["window_end"],
                    "date_label": str(item.get("date_label", "")),
                }
            )
            if len(records) >= maximum:
                return records, expected
    return records, expected


def story_urls(item: dict[str, Any]) -> set[str]:
    return {
        str(item.get("url", "")),
        *(
            str(coverage.get("url", ""))
            for coverage in item.get("also_covered", [])
        ),
    } - {""}


def score(
    briefing: dict[str, Any],
    expected: dict[str, str],
    elapsed: float,
    model: str,
    vram_mb: int | None,
    ram_mb: int | None,
) -> dict[str, Any]:
    placed: dict[str, str] = {}
    titles: list[str] = []
    summaries: list[str] = []
    for section, items in briefing["sections"].items():
        for item in items:
            titles.append(str(item.get("title", "")))
            summaries.append(str(item.get("summary", "")))
            for url in story_urls(item):
                placed.setdefault(url, section)
    matched = sum(placed.get(url) == section for url, section in expected.items())
    conflict_urls = [
        url
        for url, section in expected.items()
        if section == "Military"
        and any(word in (next((item["title"] for item in briefing["sections"].get("Military", []) if url in story_urls(item)), "")).casefold() for word in ("ukraine", "russia", "war", "battlefield", "strike"))
    ]
    return {
        "model": model,
        "valid_structured_output": True,
        "model_retries": briefing.get("model_retries", 0),
        "total_build_seconds": round(elapsed, 2),
        "gpu_vram_used_mb_after_build": vram_mb,
        "system_ram_used_mb_after_build": ram_mb,
        "supplemental_links_extracted": briefing.get("supplemental_count", 0),
        "supplemental_links_represented": briefing.get("supplemental_accounted_count", 0),
        "all_required_supplemental_represented": briefing.get("supplemental_count", 0) == briefing.get("supplemental_accounted_count", -1),
        "exact_section_placement_rate": round(matched / max(1, len(expected)), 3),
        "av_coverage": bool(briefing["sections"].get("Autonomous Vehicles")),
        "international_coverage": bool(briefing["sections"].get("International")),
        "military_conflict_urls_present": len(conflict_urls),
        "administration_wins": len(briefing["sections"].get("Trump Administration Wins", [])),
        "placeholder_headlines": sum(bool(HEADLINE_PLACEHOLDER_PATTERN.search(title)) for title in titles),
        "internal_language_in_summaries": sum(bool(STORY_SUMMARY_PROCESS_PATTERN.search(summary)) for summary in summaries),
        "executive_summary_is_public": executive_summary_sentence_is_public(str(briefing.get("executive_summary", ""))),
        "final_story_count": sum(len(items) for section, items in briefing["sections"].items() if section not in {"Trump Administration Wins", "Top Developments"}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark local Ollama models on an archived editorial corpus.")
    parser.add_argument("--archive", type=Path, default=Path("data/archive/2026-09-11.json"))
    parser.add_argument("--models", nargs="+", default=[os.environ.get("OLLAMA_MODEL", "qwen3.6:27b-q4_K_M")])
    parser.add_argument("--max-stories", type=int, default=36)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.archive.read_text(encoding="utf-8"))
    records, expected = archive_records(payload, args.max_stories)
    raw_feed = {
        "window_start": payload["window_start"],
        "window_end": payload["window_end"],
        "articles": [],
        "source_errors": [],
        "candidate_counts": {},
    }
    results: list[dict[str, Any]] = []
    for model in args.models:
        provider = OllamaProvider(
            model=model,
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            timeout_seconds=int(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "900")),
            num_ctx=int(os.environ.get("OLLAMA_NUM_CTX", "32768")),
            temperature=float(os.environ.get("OLLAMA_TEMPERATURE", "0.1")),
        )
        provider.health_check()
        started = time.monotonic()
        try:
            briefing = generate_briefing_from_records(
                raw_feed,
                records,
                "",
                model,
                provider=provider,
            )
            briefing = validate_complete_briefing(briefing)
            results.append(
                score(
                    briefing,
                    expected,
                    time.monotonic() - started,
                    model,
                    gpu_memory_used_mb(),
                    system_memory_used_mb(),
                )
            )
        except Exception as exc:
            results.append(
                {
                    "model": model,
                    "valid_structured_output": False,
                    "error": " ".join(str(exc).split())[:1000],
                    "total_build_seconds": round(time.monotonic() - started, 2),
                    "gpu_vram_used_mb_after_build": gpu_memory_used_mb(),
                    "system_ram_used_mb_after_build": system_memory_used_mb(),
                }
            )
        checkpoint = json.dumps(
            {"archive": str(args.archive), "records": len(records), "results": results},
            indent=2,
        )
        print(json.dumps(results[-1], indent=2), flush=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.output.with_suffix(args.output.suffix + ".tmp")
            temporary.write_text(checkpoint + "\n", encoding="utf-8")
            temporary.replace(args.output)
    rendered = json.dumps({"archive": str(args.archive), "records": len(records), "results": results}, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(rendered + "\n", encoding="utf-8")
        temporary.replace(args.output)


if __name__ == "__main__":
    main()
