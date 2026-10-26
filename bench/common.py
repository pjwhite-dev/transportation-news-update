"""Shared helpers for the Phase 2 summary benchmark (plan 7.10).

Every candidate model receives the identical fact sheet for a day, so differences in the
output come from the model, not the input. All models are local (owner override: no paid APIs).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_providers import OllamaProvider  # noqa: E402
from public_site import display_title  # noqa: E402

DATA = ROOT / "bench" / "data"
ARCHIVE = ROOT / "data" / "archive"

# 15 of the 19 archived editorial editions. Excluded: 07-13 (4 stories), 09-16 and 09-29 (7 stories each),
# 09-28 (4 stories, no summaries). Includes the two known-bad editions (10-21, 10-22).
BENCH_DAYS = [
    "2026-07-14", "2026-07-15", "2026-09-11", "2026-09-14", "2026-09-15", "2026-09-24", "2026-10-02",
    "2026-10-05", "2026-10-15", "2026-10-16", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-25",
    "2026-10-26",
]
KNOWN_BAD_DAYS = ["2026-10-21", "2026-10-22"]
FACT_SHEET_SIZE = 25

# name used in files -> Ollama tag
MODELS = {
    "qwen3.6": "qwen3.6:27b-q4_K_M",
    "qwen3.8": "qwen3.8:27b-q4_K_M",
    "gemma4": "gemma4:31b",
}
BASELINE = "published"


def load_edition(day: str) -> dict[str, Any]:
    return json.loads((ARCHIVE / f"{day}.json").read_text(encoding="utf-8"))


def fact_sheet(day: str) -> list[dict[str, str]]:
    """Top stories of the day with stable ids S1..Sn; identical for every candidate."""
    edition = load_edition(day)
    stories: list[dict[str, Any]] = []
    for section, items in edition.get("sections", {}).items():
        for item in items:
            if str(item.get("url", "")).startswith(("http://", "https://")) and item.get("title"):
                stories.append({**item, "_section": section})
    # Highest importance first when the edition has it; otherwise keep edition order.
    stories.sort(key=lambda s: -int(s.get("importance", 0) or 0))
    sheet = []
    for index, story in enumerate(stories[:FACT_SHEET_SIZE], start=1):
        sheet.append(
            {
                "id": f"S{index}",
                "section": str(story["_section"]),
                "headline": display_title(story),
                "source": str(story.get("source", "")),
                "date": str(story.get("date_label", "")),
                "summary": " ".join(str(story.get("summary", "")).split()),
                "url": str(story["url"]),
            }
        )
    return sheet


def fact_sheet_text(sheet: list[dict[str, str]]) -> str:
    lines = []
    for story in sheet:
        lines.append(
            f"[{story['id']}] ({story['section']}) {story['headline']}\n"
            f"    source: {story['source']} {story['date']}\n"
            f"    summary: {story['summary'] or '(none)'}\n"
            f"    url: {story['url']}"
        )
    return "\n".join(lines)


def published_summary(day: str) -> str:
    return str(load_edition(day).get("executive_summary", "")).strip()


def provider(model_tag: str, *, think: bool = False, temperature: float = 0.2, num_ctx: int = 32768,
             num_predict: int | None = None) -> OllamaProvider:
    return OllamaProvider(
        model=model_tag, think=think, temperature=temperature, num_ctx=num_ctx,
        num_predict=num_predict, stall_timeout_seconds=240, max_attempts=2, timeout_seconds=900,
    )


def sentences(text: str) -> list[str]:
    """Sentence split that keeps markdown links intact."""
    plain = re.sub(r"\s+", " ", text.strip())
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'\[(])", plain)
    return [part for part in parts if part.strip()]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
