"""Loads config/editorial.yaml once and compiles its patterns."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "editorial.yaml"


@lru_cache(maxsize=4)
def load_config(path: str = str(CONFIG_PATH)) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def compiled(patterns: list[str]) -> re.Pattern[str] | None:
    if not patterns:
        return None
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


@lru_cache(maxsize=4)
def section_scopes(path: str = str(CONFIG_PATH)) -> dict[str, dict[str, Any]]:
    scopes: dict[str, dict[str, Any]] = {}
    for name, spec in load_config(path)["sections"].items():
        scopes[name] = {
            "definition": spec.get("definition", ""),
            "origin": spec.get("origin", ""),
            "require": compiled(spec.get("require_any", [])),
            "exclude": compiled(spec.get("exclude_any", [])),
        }
    return scopes
