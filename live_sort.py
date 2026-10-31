"""Sort the stories on the page: which earlier report each one repeats, and whether it is about the United States.

One short call to the writer per story, after it is on the page: the story, plus up to eight earlier reports that
share names or figures with it. Stories of the same event share a `group_id`, and the feed shows each group once,
with the other outlets listed under it (Techmeme-style). `record["_scope"]` is "us" or "world"; the page shows
U.S. stories by default and the rest on request. A shared rule number, docket or link groups two stories without
asking the model. The writer is the model already loaded for first passes, so sorting never swaps models; it waits
while the GPU is held, and a story whose sorting fails three times stays on its own, counted as U.S.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any, Callable

import newsroom_live
import story_groups
from live_store import FIRST_PASS, VERIFIED, parse_iso

CANDIDATES = 8
BATCH = 3
MAX_TRIES = 3
WINDOW = timedelta(hours=48)

SORT_SYSTEM = """You sort ONE news report for a U.S. transportation-policy news page. Use only the text given.
us: true when the report is about the United States: U.S. government action (federal, state or local), U.S.
companies or their products, or operations, tests, crashes or events in the U.S. False for events abroad with no
U.S. government, company or place in them (a war, a foreign government's plan, a foreign company's launch abroad).
same_as: the number of the EARLIER REPORT that describes the SAME concrete event (the same announcement, rule, vote,
contract, crash, launch, strike or decision; a follow-up on that event counts), or 0 when none does. Different
events that involve the same company, agency, country or technology are NOT the same event. Respond with JSON only."""
SORT_SCHEMA = {
    "type": "object",
    "properties": {
        "us": {"type": "boolean"},
        "same_as": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["us", "same_as", "reason"],
}


def _terms(story: dict[str, Any]) -> set[str]:
    return story_groups.key_terms({"title": story["title"], "summary": story["summary"] or str(story["page_text"])[:300]})


def candidates(story: dict[str, Any], pool: list[dict[str, Any]], limit: int = CANDIDATES) -> list[dict[str, Any]]:
    """Earlier reports worth asking about: published within two days of this one and sharing names or figures."""
    when = parse_iso(story["published"])
    mine = _terms(story)
    ranked = []
    for other in pool:
        if other["id"] == story["id"] or (story["group_id"] and other["group_id"] == story["group_id"]):
            continue
        other_when = parse_iso(other["published"])
        if when and other_when and abs(when - other_when) > WINDOW:
            continue
        shared = len(mine & _terms(other))
        if shared:
            ranked.append((shared, other["published"], other))
    ranked.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return [r[2] for r in ranked[:limit]]


def describe(story: dict[str, Any]) -> str:
    summary = story["summary"] or str(story["page_text"])[:400]
    return f"{story['title']} ({story['source']})" + (f"\n    {summary[:400]}" if summary else "")


class Sorter:
    def __init__(self, desk: Any) -> None:
        self.desk = desk
        self.store = desk.store
        self.config = desk.config

    def pool(self, now) -> list[dict[str, Any]]:
        return self.store.visible(now - timedelta(hours=float(self.config["site"]["river_hours"])),
                                  limit=int(self.config["site"].get("feed_limit", 300)))

    def sort_once(self) -> int:
        """Sort up to BATCH unsorted stories on the page, newest first; how many were sorted."""
        if self.desk.level == "HEADLINES" or self.desk.gpu_busy():
            return 0
        now = self.desk.clock()
        pool = self.pool(now)
        waiting = [s for s in pool if not s["record"].get("_sorted")][:BATCH]
        done = 0
        for story in waiting:
            if self.sort(story, pool, now):
                done += 1
        return done

    def sort(self, story: dict[str, Any], pool: list[dict[str, Any]], now) -> bool:
        options = candidates(story, pool)
        lexical = next((o for o in options if story_groups.same_event(story, o)[0]), None)
        try:
            with self.desk.model_slots:
                result = self.desk.writer.generate_structured(
                    [{"role": "system", "content": SORT_SYSTEM},
                     {"role": "user", "content": "REPORT\n" + describe(story) + "\n\nEARLIER REPORTS\n"
                      + ("\n".join(f"[{i}] {describe(o)}" for i, o in enumerate(options, 1)) or "(none)")}],
                    schema_name="live_sort", schema=SORT_SCHEMA, max_output_tokens=200,
                )
        except Exception as exc:  # noqa: BLE001 - a model outage must never stop the page
            self.desk.health.fail("writer")
            tries = int(story["record"].get("_sort_tries", 0)) + 1
            record = {**story["record"], "_sort_tries": tries}
            if tries >= MAX_TRIES:
                record.update(_sorted=True, _scope="us")
            self.store.update(story["id"], now, record=record)
            newsroom_live.emit("live-sort-fail", desk="editors", title=story["title"][:120], error=str(exc)[:200])
            return False
        self.desk.health.ok("writer")
        value = result.value
        pick = int(value.get("same_as") or 0)
        twin = lexical or (options[pick - 1] if 1 <= pick <= len(options) else None)
        scope = "us" if value.get("us", True) else "world"
        self.store.update(story["id"], now, record={**story["record"], "_sorted": True, "_scope": scope})
        if twin is not None:
            self.join(story, twin, now)
        newsroom_live.emit("live-sort", desk="editors", title=story["title"][:120], scope=scope,
                           same_as=twin["title"][:120] if twin else "", reason=str(value.get("reason", ""))[:160])
        story["record"]["_sorted"] = True
        return True

    def join(self, story: dict[str, Any], twin: dict[str, Any], now) -> None:
        """Put the story (and any group it already heads) into the twin's group."""
        group = twin["group_id"] or twin["id"]
        if not twin["group_id"]:
            self.store.update(twin["id"], now, group_id=group)
            twin["group_id"] = group
        old = story["group_id"]
        for member in self.pool(now):
            if member["id"] == story["id"] or (old and member["group_id"] == old):
                self.store.update(member["id"], now, group_id=group)
        story["group_id"] = group

    def run(self, stop: threading.Event, beat: Callable[[], None] = lambda: None) -> None:
        while not stop.is_set():
            beat()
            try:
                sorted_now = self.sort_once()
            except Exception as exc:  # noqa: BLE001 - the doctor restarts a dead worker, but a bad row should not kill it
                newsroom_live.emit("live-sort-fail", desk="editors", error=repr(exc)[:200])
                sorted_now = 0
            stop.wait(2 if sorted_now else 20)


def primary_rank(story: dict[str, Any]) -> tuple:
    """The member a group is shown as: the most-checked summary, then an official source, then the first report."""
    status = {VERIFIED: 2, FIRST_PASS: 1}.get(story["status"], 0)
    shown = parse_iso(story["shown_at"])
    return (status, int(bool(story["official"])), -story_groups.source_tier(story), -(shown.timestamp() if shown else 0))
