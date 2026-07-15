from __future__ import annotations

from pathlib import Path

from news_engine import generate_raw_feed, write_raw_feed


def main() -> None:
    root = Path(__file__).resolve().parent
    raw_feed = generate_raw_feed()
    latest, archive = write_raw_feed(raw_feed, root)

    print(f"Wrote {latest}")
    print(f"Archived {archive}")
    print(
        f"Coverage: {raw_feed['window_start']} through {raw_feed['window_end']} | "
        f"Raw candidates: {raw_feed.get('candidate_count', 0)}"
    )


if __name__ == "__main__":
    main()
