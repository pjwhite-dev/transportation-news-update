"""Live events for the local newsroom dashboard (newsroom_dashboard.py).

Every pipeline step appends one JSON line per event to .runtime/live/events.jsonl; the dashboard
tails that file. Best effort only: an event that cannot be written is dropped, never raised, so
the dashboard can never slow or break an edition.

    emit("verdict", desk="factcheck", title=..., outcome="spiked", reason=...)

Command line (for the PowerShell scripts):
    python newsroom_live.py run-start edition|rehearsal|doctor "label"
    python newsroom_live.py run-end ok|failed "reason"
    python newsroom_live.py event <desk> <kind> "text"
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent
MAX_BYTES = 30_000_000  # rotate before a run once the file passes this
INK_FLUSH_SECONDS = 0.25
INK_FLUSH_CHARS = 240

# The newsroom's desks, in the order a story moves through them.
DESKS = ("wire", "factcheck", "editors", "rewrite", "standards", "layout", "press", "delivery", "doctor")

# What each model call is for, by schema name -> (desk, plain label).
PURPOSES = {
    "transportation_news_analysis": ("editors", "Choosing the stories"),
    "story_summary": ("rewrite", "Writing a story summary"),
    "entailment": ("rewrite", "Checking the summary against the article"),
    "same_event": ("standards", "Is this the same story?"),
    "topic_check": ("standards", "Is this really on topic?"),
    "headline_translation": ("factcheck", "Translating a headline"),
    "prose": ("layout", "Writing the Executive Summary"),
    "summary_support": ("layout", "Checking every Executive Summary sentence"),
    "diagnosis": ("doctor", "Diagnosing the failure"),
}

_lock = threading.Lock()
_local = threading.local()


def events_path() -> Path:
    return Path(os.environ.get("NEWSROOM_LIVE_DIR") or ROOT / ".runtime" / "live") / "events.jsonl"


def emit(kind: str, desk: str = "", **data: Any) -> None:
    # Unit tests stay off the dashboard unless a test turns it on.
    if os.environ.get("NEWSROOM_LIVE") == "off" or ("unittest" in sys.modules and os.environ.get("NEWSROOM_LIVE") != "on"):
        return
    try:
        event = {"t": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "kind": kind, "desk": desk, **data}
        line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
        path = events_path()
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
    except Exception:  # noqa: BLE001 - the dashboard must never break an edition
        pass


def run_start(mode: str, label: str = "") -> None:
    """Begin a run on the dashboard (edition, rehearsal or doctor); child processes join it via the environment."""
    try:
        path = events_path()
        if path.exists() and path.stat().st_size > MAX_BYTES:
            path.replace(path.with_name("events.prev.jsonl"))
    except OSError:
        pass
    os.environ["NEWSROOM_RUN_ACTIVE"] = "1"
    emit("run-start", mode=mode, label=label, pid=os.getpid())


def run_end(status: str, reason: str = "") -> None:
    emit("run-end", status=status, reason=reason[:600])


def in_run() -> bool:
    return os.environ.get("NEWSROOM_RUN_ACTIVE") == "1"


def desk(name: str, text: str = "", **data: Any) -> None:
    """A desk takes over the edition."""
    emit("desk", desk=name, text=text, **data)


@contextmanager
def purpose(schema_name: str, subject: str = "") -> Iterator[None]:
    previous = getattr(_local, "purpose", None)
    _local.purpose = (schema_name, subject)
    try:
        yield
    finally:
        _local.purpose = previous


def story_count(stories: Any) -> int:
    """Stories in a list, or in a {section: [stories]} mapping."""
    try:
        return sum(len(v) for v in stories.values()) if isinstance(stories, dict) else len(stories)
    except Exception:  # noqa: BLE001
        return 0


def wire_feed(source: str, query: str, items: list[dict[str, Any]], error: str | None = None, **data: Any) -> None:
    """One news search done: what it brought in (a few headlines for the ticker)."""
    emit("feed", desk="wire", source=source, query=query[:160], found=len(items), error=(error or "")[:200],
         sample=[{"title": str(i.get("title", ""))[:140], "source": str(i.get("source", ""))[:60]} for i in items[:4]], **data)


def subject_from(messages: list[dict[str, Any]]) -> str:
    """What a model call is about, for the notepad's header: the first headline it was given, else its opening words."""
    try:
        text = next((str(m.get("content", "")) for m in reversed(messages) if m.get("role") == "user"), "")
        records = text.count('"id":')
        if records > 1:
            return f"A batch of {records} candidate stories"
        for line in text.splitlines():
            stripped = line.strip().lstrip("-* ")
            for marker in ("Headline:", "HEADLINE:", "Title:", '"title": "'):
                if stripped.startswith(marker):
                    return stripped[len(marker):].strip().strip('",')[:200]
        return " ".join(text.split())[:160]
    except Exception:  # noqa: BLE001
        return ""


class Ink:
    """A model's output as it is written, sent in small pieces (the dashboard types it out)."""

    def __init__(self, model: str, schema_name: str = "", subject: str = "") -> None:
        current = getattr(_local, "purpose", None) or (schema_name, subject)
        name = current[0] or schema_name or "prose"
        self.desk, self.label = PURPOSES.get(name, ("rewrite", "Writing"))
        self.id = f"{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}"
        self.model, self.buffer, self.total, self.last = model, "", 0, time.monotonic()
        emit("ink-start", desk=self.desk, id=self.id, model=model, label=self.label, purpose=name,
             subject=(current[1] or subject)[:240])

    def write(self, text: str) -> None:
        if not text:
            return
        self.buffer += text
        self.total += len(text)
        if len(self.buffer) >= INK_FLUSH_CHARS or time.monotonic() - self.last >= INK_FLUSH_SECONDS:
            self.flush()

    def flush(self) -> None:
        if self.buffer:
            emit("ink", desk=self.desk, id=self.id, text=self.buffer)
            self.buffer, self.last = "", time.monotonic()

    def close(self, ok: bool = True, error: str = "") -> None:
        self.flush()
        emit("ink-end", desk=self.desk, id=self.id, ok=ok, chars=self.total, error=error[:200])


# QA stages -> the desk that made the call and the stamp it shows.
QA_STAMPS = {
    "non-article-url": ("factcheck", "SPIKED"), "article-check": ("factcheck", "SPIKED"),
    "scope": ("standards", "OFF-TOPIC"), "group": ("standards", "MERGED"), "cross-edition": ("standards", "REPEAT"),
    "summary-regenerated": ("rewrite", "REWRITTEN"), "summary-fallback": ("rewrite", "FALLBACK"),
    "edition-cap": ("layout", "CUT FOR SPACE"), "official-source": ("delivery", "OFFICIAL SOURCE"),
    "lead-blocked": ("layout", "NOT THE LEAD"), "link-gate": ("press", "DEAD LINK"), "edition-refused": ("press", "REFUSED"),
}


def qa_event(event: dict[str, Any]) -> None:
    stage = str(event.get("stage", ""))
    desk_name, stamp = QA_STAMPS.get(stage, ("standards", stage.upper()))
    reason = str(event.get("reason", ""))
    if stage == "scope" and reason.startswith("moved"):
        stamp = "MOVED"
    emit("stamp", desk=desk_name, stamp=stamp, stage=stage, title=str(event.get("title", ""))[:200],
         url=str(event.get("url", "")), reason=reason[:300])


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "run-start":
        run_start(argv[2] if len(argv) > 2 else "edition", argv[3] if len(argv) > 3 else "")
    elif len(argv) >= 2 and argv[1] == "run-end":
        run_end(argv[2] if len(argv) > 2 else "ok", argv[3] if len(argv) > 3 else "")
    elif len(argv) >= 5 and argv[1] == "event":
        emit(argv[3], desk=argv[2], text=argv[4])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
