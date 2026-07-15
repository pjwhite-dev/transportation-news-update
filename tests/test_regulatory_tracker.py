from __future__ import annotations

from datetime import date
import unittest

from regulatory_tracker import build_regulatory_tracker


class RegulatoryTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.items = {
            item["id"]: item
            for item in build_regulatory_tracker(date(2026, 7, 15))
        }

    def test_required_rulemakings_are_present(self) -> None:
        self.assertTrue(
            {
                "bvlos-part-108",
                "section-2209-uafr",
                "supersonic-overland-flight",
                "fmvss-102-ads",
                "fmvss-103-104-ads",
                "fmvss-110-ads",
                "zoox-part-555",
            }
            <= set(self.items)
        )
        self.assertIn("FMVSS Nos. 103, 104, 108", self.items["zoox-part-555"]["action"])

    def test_open_periods_show_current_days_remaining(self) -> None:
        section_2209 = self.items["section-2209-uafr"]
        supersonic = self.items["supersonic-overland-flight"]

        self.assertEqual(section_2209["comment_deadline"], "2026-08-05")
        self.assertEqual(section_2209["days_remaining"], 21)
        self.assertEqual(section_2209["status"], "Open for comment")
        self.assertEqual(supersonic["comment_deadline"], "2026-08-17")
        self.assertEqual(supersonic["days_remaining"], 33)
        self.assertEqual(supersonic["status"], "Open for comment")

    def test_closed_period_retains_closure_date_and_pending_status(self) -> None:
        bvlos = self.items["bvlos-part-108"]

        self.assertIsNone(bvlos["days_remaining"])
        self.assertEqual(bvlos["comment_period_closed_on"], "February 11, 2026")
        self.assertEqual(bvlos["status"], "Pending final rule")

    def test_all_items_link_to_official_federal_register_records(self) -> None:
        for item in self.items.values():
            with self.subTest(item=item["id"]):
                self.assertTrue(
                    item["source_url"].startswith(
                        "https://www.federalregister.gov/"
                    )
                )


if __name__ == "__main__":
    unittest.main()
