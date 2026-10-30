"""Significance score and the lead rule for the live site (deterministic; the model only rates two inputs).

The lead bar starts just above the current lead's score and decays toward a floor, so a quiet day turns the
lead over in roughly a day while a very big story replaces it within the hour:

    bar(h) = FLOOR + (S_lead + MARGIN - FLOOR) * exp(-h / TAU)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

FLOOR = 50.0
MARGIN = 8.0
TAU_HOURS = 8.0
MIN_DWELL = timedelta(minutes=45)
BIG_STORY = 90
BIG_STORY_DWELL = timedelta(minutes=20)
BIG_LEAD_PROTECTION = timedelta(hours=3)
MAX_LEAD_AGE = timedelta(hours=12)  # a story older than this cannot take the lead
STALE_LEAD = timedelta(hours=30)  # after this the best verified story of the last day takes over
BEST_OF_WINDOW = timedelta(hours=24)

# Model picks the class; the points are fixed here.
ACTION_POINTS = {
    "final_rule": 15, "law": 15, "first_commercial_operation": 15, "fatal_accident": 15,
    "proposed_rule": 10, "major_award": 10,
    "test": 6,
    "plan": 4,
    "other": 0,
}
ACTION_CLASSES = tuple(ACTION_POINTS)
PENALTY_POINTS = {
    "press_release_no_event": 12,
    "opinion": 15,
    "older_than_24h": 10,
    "single_weak_source": 10,
}


@dataclass
class Score:
    total: int
    parts: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"total": self.total, "parts": dict(self.parts)}


def significance(
    *,
    impact: int,
    action: str,
    pickup_outlets: int,
    official: bool,
    us_relevance: int,
    novel: bool,
    penalties: list[str] | tuple[str, ...] = (),
) -> Score:
    """Combine the model's two ratings (impact 0-5, action class) with code-measured factors into 0-100."""
    parts = {
        "impact": max(0, min(5, int(impact))) * 8,
        "action": ACTION_POINTS.get(action, 0),
        "pickup": min(15, 5 * max(0, int(pickup_outlets))),
        "official": 10 if official else 0,
        "us": max(0, min(10, int(us_relevance))),
        "novelty": 10 if novel else 0,
    }
    for name in penalties:
        parts[f"penalty:{name}"] = -PENALTY_POINTS.get(name, 10)
    return Score(max(0, min(100, sum(parts.values()))), parts)


def bar(lead_score: float, hours: float) -> float:
    """The score a new story must reach to replace a lead that has stood `hours`."""
    return FLOOR + (lead_score + MARGIN - FLOOR) * math.exp(-max(0.0, hours) / TAU_HOURS)


@dataclass
class Decision:
    swap: bool
    reason: str
    bar: float | None = None


def decide(
    lead: dict[str, Any] | None,
    candidate: dict[str, Any],
    now: datetime,
) -> Decision:
    """Should `candidate` replace `lead`? Both are dicts with `score`, `published`, `verified`; lead also `set_at`."""
    if not candidate.get("verified"):
        return Decision(False, "only verified stories can lead")
    published = candidate.get("published")
    if isinstance(published, datetime) and now - published > MAX_LEAD_AGE:
        return Decision(False, "older than 12 hours")
    score = int(candidate.get("score") or 0)
    if lead is None:
        return Decision(True, "no current lead")
    if lead.get("story_id") and lead.get("story_id") == candidate.get("story_id"):
        return Decision(False, "already the lead")
    held = now - lead["set_at"]
    lead_score = int(lead.get("score") or 0)
    hours = held.total_seconds() / 3600
    needed = bar(lead_score, hours)
    if score >= BIG_STORY and held >= BIG_STORY_DWELL and not (lead_score >= BIG_STORY and held < BIG_LEAD_PROTECTION):
        return Decision(True, f"{score} is a 90+ story and the lead has stood {held_text(held)}", needed)
    if held < MIN_DWELL:
        return Decision(False, f"the lead has stood only {held_text(held)} (minimum 45 minutes)", needed)
    if score >= needed:
        return Decision(True, f"{score} clears the bar of {needed:.0f} after {held_text(held)}", needed)
    return Decision(False, f"{score} is below the bar of {needed:.0f}", needed)


def stale_takeover(lead: dict[str, Any] | None, pool: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    """After 30 hours the best verified story of the last day takes over (None when there is none)."""
    if lead is None or now - lead["set_at"] < STALE_LEAD:
        return None
    fresh = [
        s for s in pool
        if s.get("verified") and s.get("story_id") != lead.get("story_id")
        and isinstance(s.get("published"), datetime) and now - s["published"] <= BEST_OF_WINDOW
    ]
    return max(fresh, key=lambda s: (int(s.get("score") or 0), s["published"]), default=None)


def held_text(held: timedelta) -> str:
    minutes = int(held.total_seconds() // 60)
    return f"{minutes} min" if minutes < 120 else f"{minutes / 60:.1f} h"
