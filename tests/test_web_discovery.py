from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import requests

from web_discovery import fetch_searxng, normalize_url


EASTERN = ZoneInfo("America/New_York")


class WebDiscoveryTests(unittest.TestCase):
    def test_url_cleanup_fixes_msn_and_tracking_parameters(self) -> None:
        self.assertEqual(
            normalize_url("<https://msn.om/story?id=7&utm_source=email>"),
            "https://msn.com/story?id=7",
        )

    @patch("web_discovery.requests.get")
    def test_searxng_failure_is_reported_without_raising(self, get: Mock) -> None:
        get.side_effect = requests.ConnectionError("offline")
        start = datetime(2026, 9, 10, 8, tzinfo=EASTERN)
        end = datetime(2026, 9, 11, 8, tzinfo=EASTERN)

        items, errors = fetch_searxng("http://localhost:8080", start, end)

        self.assertEqual(items, [])
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
