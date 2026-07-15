from __future__ import annotations

import unittest

from supplemental_email import extract_supplemental_items, is_source_only


class SupplementalExtractionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
