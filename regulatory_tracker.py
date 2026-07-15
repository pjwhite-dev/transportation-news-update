from __future__ import annotations

from datetime import date, datetime


# Curated from official Federal Register records and verified July 15, 2026.
# When an agency extends a deadline or completes an action, update the applicable
# deadline, post-comment status, and source URL to the newest official notice.
TRACKED_RULEMAKINGS = (
    {
        "id": "section-2209-uafr",
        "agency": "FAA",
        "action": "Section 2209 fixed-site UAS flight restrictions / Part 74",
        "docket": "FAA-2026-4558",
        "comment_deadline": "2026-08-05",
        "post_comment_status": "Pending final rule",
        "source_url": "https://www.federalregister.gov/d/2026-13126",
    },
    {
        "id": "supersonic-overland-flight",
        "agency": "FAA",
        "action": (
            "Civil supersonic overland flight / interim noise standard"
        ),
        "docket": "FAA-2026-6935",
        "comment_deadline": "2026-08-17",
        "post_comment_status": "Pending final rule",
        "source_url": "https://www.federalregister.gov/d/2026-13440",
    },
    {
        "id": "bvlos-part-108",
        "agency": "FAA and TSA",
        "action": "Routine BVLOS drone operations / Part 108",
        "docket": "FAA-2025-1908",
        "comment_deadline": "2026-02-11",
        "post_comment_status": "Pending final rule",
        "source_url": "https://www.federalregister.gov/d/2026-02649",
    },
    {
        "id": "fmvss-102-ads",
        "agency": "NHTSA",
        "action": (
            "FMVSS No. 102 for ADS-equipped vehicles without manual "
            "driving controls"
        ),
        "docket": "NHTSA-2026-0628",
        "comment_deadline": "2026-04-15",
        "post_comment_status": "Pending NHTSA action",
        "source_url": "https://www.federalregister.gov/d/2026-05024",
    },
    {
        "id": "fmvss-103-104-ads",
        "agency": "NHTSA",
        "action": (
            "FMVSS Nos. 103 and 104 for ADS-equipped vehicles without "
            "manual driving controls"
        ),
        "docket": "NHTSA-2026-0629",
        "comment_deadline": "2026-04-15",
        "post_comment_status": "Pending NHTSA action",
        "source_url": "https://www.federalregister.gov/d/2026-05023",
    },
    {
        "id": "fmvss-110-ads",
        "agency": "NHTSA",
        "action": "FMVSS No. 110 for ADS-equipped vehicles",
        "docket": "NHTSA-2026-0630",
        "comment_deadline": "2026-05-01",
        "post_comment_status": "Pending NHTSA action",
        "source_url": "https://www.federalregister.gov/d/2026-06254",
    },
    {
        "id": "zoox-part-555",
        "agency": "NHTSA",
        "action": (
            "Zoox Part 555 temporary exemption petition / FMVSS "
            "Nos. 103, 104, 108, 111, 135, 201, 205, and 208"
        ),
        "docket": "NHTSA-2025-0523",
        "comment_deadline": "2026-04-10",
        "post_comment_status": "Pending petition decision",
        "source_url": "https://www.federalregister.gov/d/2026-04730",
    },
)


def format_tracker_date(value: date) -> str:
    return value.strftime("%B %d, %Y").replace(" 0", " ")


def build_regulatory_tracker(as_of: date | datetime) -> list[dict]:
    as_of_date = as_of.date() if isinstance(as_of, datetime) else as_of
    tracker = []

    for definition in TRACKED_RULEMAKINGS:
        item = dict(definition)
        deadline = date.fromisoformat(item["comment_deadline"])
        days_remaining = (deadline - as_of_date).days
        is_open = days_remaining >= 0

        item.update(
            {
                "comment_deadline_label": format_tracker_date(deadline),
                "comment_period_closed_on": (
                    "" if is_open else format_tracker_date(deadline)
                ),
                "days_remaining": days_remaining if is_open else None,
                "status": (
                    "Open for comment"
                    if is_open
                    else item["post_comment_status"]
                ),
            }
        )
        tracker.append(item)

    return tracker
