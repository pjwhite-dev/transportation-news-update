from __future__ import annotations

import unittest
from unittest.mock import patch

from supplemental_email import (
    clean_headline_candidate,
    extract_supplemental_items,
    fetch_link_metadata,
    is_source_only,
)


class SupplementalExtractionTests(unittest.TestCase):
    def test_headline_cleaning_preserves_rule_numbers(self) -> None:
        self.assertEqual(
            clean_headline_candidate("2209 Rule Advances Fixed-Site Protections"),
            "2209 Rule Advances Fixed-Site Protections",
        )

    def test_malformed_wrappers_punctuation_and_duplicate_urls_are_cleaned(
        self,
    ) -> None:
        pasted = """
FAA approves expanded BVLOS drone operations
<https://example.com/article>).
Duplicate coverage https://example.com/article.,
New autonomous-trucking permit issued
www.example.org/av-permit.
"""

        records = extract_supplemental_items(pasted, fetch_metadata=False)

        self.assertEqual(
            [item["url"] for item in records],
            [
                "https://example.com/article",
                "https://www.example.org/av-permit",
            ],
        )
        self.assertEqual(
            records[0]["title"],
            "FAA approves expanded BVLOS drone operations",
        )
        self.assertTrue(all(item["required_include"] for item in records))
        self.assertTrue(all(item["editor_vetted"] for item in records))

    def test_publisher_line_is_not_used_as_pasted_headline(self) -> None:
        pasted = """
FAA authorizes a new drone delivery route
MSN
<https://www.msn.com/drone-route>
"""

        records = extract_supplemental_items(pasted, fetch_metadata=False)

        self.assertTrue(is_source_only("MSN", "MSN"))
        self.assertEqual(
            records[0]["title"],
            "FAA authorizes a new drone delivery route",
        )

    def test_url_in_description_does_not_turn_description_into_headline(self) -> None:
        pasted = """
FAA authorizes routine BVLOS drone operations
MSN
It's creating opportunities not only for drone operators but for communities. <https://example.com/article>
"""

        records = extract_supplemental_items(pasted, fetch_metadata=False)

        self.assertEqual(
            records[0]["title"],
            "FAA authorizes routine BVLOS drone operations",
        )
        self.assertNotIn("https://", records[0]["pasted_context"])
        self.assertNotIn("<", records[0]["pasted_context"])
        self.assertNotIn(">", records[0]["pasted_context"])

    @patch("supplemental_email.requests.get")
    def test_article_metadata_title_overrides_nearby_pasted_prose(
        self,
        mock_get,
    ) -> None:
        class Response:
            url = "https://example.com/article"
            text = (
                '<meta property="og:title" '
                'content="FAA&#x27;s Actual Article Headline | Example">'
                '<meta name="description" content="Article description.">'
            )

            @staticmethod
            def raise_for_status() -> None:
                return None

        mock_get.return_value = Response()
        enriched = fetch_link_metadata(
            {
                "url": "https://example.com/article",
                "pasted_headline": (
                    "It's creating opportunities not only for drone operators."
                ),
                "pasted_context": "Nearby description text.",
            }
        )

        self.assertEqual(enriched["title"], "FAA's Actual Article Headline")
        self.assertEqual(
            enriched["original_title"],
            "FAA's Actual Article Headline | Example",
        )


if __name__ == "__main__":
    unittest.main()
