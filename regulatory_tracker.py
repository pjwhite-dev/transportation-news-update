"""Regulatory Deadline Tracker entry point.

The watch list lives in tracker/rulemakings.yaml; statuses are derived from the
append-only Federal Register event ledger in tracker/ (see tracker/core.py).
"""

from __future__ import annotations

from datetime import date, datetime

from tracker.core import build_tracker


def build_regulatory_tracker(as_of: date | datetime) -> list[dict]:
    """Offline and deterministic; run ``python -m tracker.core`` to refresh the ledger."""
    return build_tracker(as_of)
