from __future__ import annotations

import argparse
import json
from pathlib import Path

from automated_briefing import normalize_reader_features
from news_engine import executive_summary_sentence_is_public


def validate_owner_edit(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("The edited edition must be a JSON object.")
    normalized = normalize_reader_features(payload)
    if not executive_summary_sentence_is_public(
        normalized.get("executive_summary", "")
    ):
        raise ValueError(
            "The Executive Summary contains internal editorial-process language."
        )
    return normalized


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate an owner-edited edition.")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    validate_owner_edit(args.path)
    print("Owner-edited edition is valid.")


if __name__ == "__main__":
    main()
