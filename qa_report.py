"""Per-edition QA report: every item dropped, moved or regenerated, and why.

    qa/YYYY-MM-DD.json   machine-readable
    qa/YYYY-MM-DD.md     the short version for a person
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


class QAReport:
    def __init__(self, edition: str) -> None:
        self.edition = edition
        self.events: list[dict[str, Any]] = []
        self.notes: dict[str, Any] = {}

    def add(self, stage: str, title: str, reason: str, url: str = "", **extra: Any) -> None:
        self.events.append({"stage": stage, "title": title[:160], "url": url, "reason": reason, **extra})

    def extend(self, items: list[dict[str, str]]) -> None:
        for item in items:
            self.events.append({k: v for k, v in item.items()})

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QAReport":
        report = cls(str(data["edition"]))
        report.events = list(data.get("events", []))
        report.notes = dict(data.get("notes", {}))
        return report

    def note(self, key: str, value: Any) -> None:
        self.notes[key] = value

    def summary(self) -> dict[str, int]:
        return dict(Counter(event["stage"] for event in self.events))

    def to_dict(self) -> dict[str, Any]:
        return {"edition": self.edition, "summary": self.summary(), "notes": self.notes, "events": self.events}

    def to_markdown(self) -> str:
        lines = [f"# QA report {self.edition}", ""]
        for stage, count in sorted(self.summary().items()):
            lines.append(f"- {stage}: {count}")
        for key, value in self.notes.items():
            lines.append(f"- {key}: {value}")
        lines.append("")
        by_stage: dict[str, list[dict[str, Any]]] = {}
        for event in self.events:
            by_stage.setdefault(event["stage"], []).append(event)
        for stage, events in by_stage.items():
            lines.append(f"## {stage} ({len(events)})")
            for event in events:
                link = f" <{event['url']}>" if event.get("url") else ""
                lines.append(f"- {event['title']}{link} - {event['reason']}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def write(self, root: Path) -> tuple[Path, Path]:
        directory = root / "qa"
        directory.mkdir(parents=True, exist_ok=True)
        json_path, md_path = directory / f"{self.edition}.json", directory / f"{self.edition}.md"
        json_path.write_text(json.dumps(self.to_dict(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        md_path.write_text(self.to_markdown(), encoding="utf-8")
        return json_path, md_path
