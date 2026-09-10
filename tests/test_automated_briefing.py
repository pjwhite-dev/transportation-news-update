from __future__ import annotations

import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import automated_briefing


class AutomatedBriefingTests(unittest.TestCase):
    def test_reads_base64_supplemental_email_from_repository_dispatch(self) -> None:
        text = "Headline\n<https://example.com/story>"
        event = {
            "client_payload": {
                "supplemental_email_b64": base64.b64encode(
                    text.encode("utf-8")
                ).decode("ascii")
            }
        }
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "event.json"
            path.write_text(json.dumps(event), encoding="utf-8")

            self.assertEqual(
                automated_briefing.supplemental_text_from_event(path),
                text,
            )

    def test_normalization_requires_summary_and_adds_innovative_use(self) -> None:
        briefing = {
            "executive_summary": "Transportation agencies advanced several initiatives.",
            "sections": {
                "UAS and Drones": [
                    {
                        "title": "Drones begin delivering blood to rural clinics",
                        "summary": "The service carries urgent medical supplies.",
                        "innovative_uas_use": "",
                    }
                ]
            },
            "regulatory_tracker": [],
            "what_to_watch": [],
        }

        normalized = automated_briefing.normalize_reader_features(briefing)

        self.assertEqual(normalized["edition_kind"], "editorial")
        self.assertTrue(
            normalized["sections"]["UAS and Drones"][0]["innovative_uas_use"]
        )
        self.assertIn("Trump Administration Wins", normalized["sections"])

    def test_build_uses_history_and_supplemental_records(self) -> None:
        generated = {
            "window_start": "2026-09-09T04:15:00-04:00",
            "window_end": "2026-09-10T04:15:00-04:00",
            "executive_summary": "A complete briefing summary.",
            "sections": {},
            "regulatory_tracker": [],
            "what_to_watch": [],
        }
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data" / "archive").mkdir(parents=True)
            (root / "data" / "latest_raw_news.json").write_text(
                json.dumps(
                    {
                        "window_start": "2026-09-09T04:15:00-04:00",
                        "window_end": "2026-09-10T04:15:00-04:00",
                        "articles": [],
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch(
                    "automated_briefing.extract_supplemental_items",
                    return_value=[{"id": "supplemental"}],
                ) as extract,
                patch(
                    "automated_briefing.generate_briefing_from_records",
                    return_value=generated,
                ) as generate,
            ):
                briefing = automated_briefing.build_briefing(
                    root,
                    api_key="test-key",
                    model="test-model",
                    supplemental_text="https://example.com/story",
                    fetch_metadata=False,
                )

        extract.assert_called_once_with(
            "https://example.com/story",
            fetch_metadata=False,
        )
        self.assertEqual(generate.call_args.args[1], [{"id": "supplemental"}])
        self.assertEqual(generate.call_args.args[2], "test-key")
        self.assertEqual(briefing["publication_mode"], "automated-ai")

    def test_save_writes_latest_and_dated_archive(self) -> None:
        briefing = {
            "window_end": "2026-09-10T04:15:00-04:00",
            "sections": {},
        }
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            latest, archived = automated_briefing.save_briefing(root, briefing)

            self.assertTrue(latest.exists())
            self.assertEqual(archived.name, "2026-09-10.json")
            self.assertEqual(latest.read_text(), archived.read_text())


if __name__ == "__main__":
    unittest.main()
