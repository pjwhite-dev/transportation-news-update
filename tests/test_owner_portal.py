from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "owner_portal" / "api" / "index.py"
SPEC = importlib.util.spec_from_file_location("owner_portal_api", MODULE_PATH)
assert SPEC and SPEC.loader
owner_api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(owner_api)


def sample_briefing() -> dict:
    return {
        "generated_at": "2026-09-11T08:30:00-04:00",
        "window_end": "2026-09-11T08:26:00-04:00",
        "executive_summary": "FAA and industry advanced several transportation programs today.",
        "sections": {
            section: [] for section in owner_api.SECTION_ORDER
        },
        "regulatory_tracker": [],
        "what_to_watch": ["FAA implementation milestones"],
    }


class OwnerSessionTests(unittest.TestCase):
    def test_session_cookie_round_trip_and_expiration(self) -> None:
        with patch.dict(os.environ, {"SESSION_SECRET": "s" * 48}, clear=False):
            cookie = owner_api.create_session_cookie(now=1_000)
            self.assertTrue(owner_api.session_is_valid(cookie, now=1_001))
            self.assertFalse(
                owner_api.session_is_valid(
                    cookie,
                    now=1_000 + owner_api.SESSION_SECONDS + 1,
                )
            )

    def test_tampered_session_cookie_is_rejected(self) -> None:
        with patch.dict(os.environ, {"SESSION_SECRET": "s" * 48}, clear=False):
            cookie = owner_api.create_session_cookie(now=1_000)
            name_value, attributes = cookie.split(";", 1)
            name, value = name_value.split("=", 1)
            version, expires, signature = value.split(".", 2)
            replacement = "0" if signature[-1] != "0" else "1"
            tampered = (
                f"{name}={version}.{expires}.{signature[:-1]}{replacement};"
                f"{attributes}"
            )
            self.assertFalse(owner_api.session_is_valid(tampered, now=1_001))

    def test_short_session_secret_is_rejected(self) -> None:
        with patch.dict(os.environ, {"SESSION_SECRET": "short"}, clear=False):
            with self.assertRaisesRegex(owner_api.OwnerPortalError, "securely"):
                owner_api.create_session_cookie(now=1_000)


class OwnerEditValidationTests(unittest.TestCase):
    def test_valid_edit_is_normalized(self) -> None:
        briefing = sample_briefing()
        briefing["sections"]["UAS and Drones"].append(
            {
                "title": "Water-rescue drones begin county testing",
                "summary": "The aircraft can drop flotation equipment.",
                "source": "Example",
                "url": "https://example.com/rescue",
            }
        )
        result = owner_api.validate_briefing(briefing)
        self.assertEqual(result["publication_mode"], "owner-edited")
        self.assertEqual(
            result["sections"]["UAS and Drones"][0]["title"],
            "Water-rescue drones begin county testing",
        )

    def test_process_language_is_rejected_from_executive_summary(self) -> None:
        briefing = sample_briefing()
        briefing["executive_summary"] = "The supplemental links were represented."
        with self.assertRaisesRegex(owner_api.OwnerPortalError, "process language"):
            owner_api.validate_briefing(briefing)

    def test_invalid_story_link_is_rejected(self) -> None:
        briefing = sample_briefing()
        briefing["sections"]["Military"].append(
            {"title": "Story", "summary": "Summary", "url": "javascript:alert(1)"}
        )
        with self.assertRaisesRegex(owner_api.OwnerPortalError, "link.*invalid"):
            owner_api.validate_briefing(briefing)


if __name__ == "__main__":
    unittest.main()
