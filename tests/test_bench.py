from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bench import common, judge, metrics, report

URL_A, URL_B, URL_C = "https://a.example/1", "https://b.example/2", "https://c.example/3"


def good_text() -> str:
    para = (
        "The Federal Aviation Administration proposed a rule for routine remote drone flights, and operators "
        "said the proposal would let them fly [beyond visual line of sight](%s) at scale across the country, "
        "which regulators said would cut costs for delivery and inspection businesses over several years, while "
        "agency officials added that safety reviews and public comment would continue through the winter and into the spring. "
    )
    body = "\n\n".join(para % url for url in (URL_A, URL_B, URL_C)) + "\n\n" + para % URL_A
    return body


class MetricsTests(unittest.TestCase):
    ALLOWED = {URL_A, URL_B, URL_C}

    def test_good_text_passes_shape(self) -> None:
        result = metrics.check(good_text(), self.ALLOWED)
        self.assertTrue(result["passes_shape"], result)
        self.assertEqual(result["links"], 4)
        self.assertEqual(result["invented_urls"], [])

    def test_failures_are_detected(self) -> None:
        self.assertIn("https://evil.example/x", metrics.check(good_text() + " [x](https://evil.example/x)", self.ALLOWED)["invented_urls"])
        self.assertEqual(metrics.check("Key developments include A; B.", self.ALLOWED)["template_text"] != [], True)
        self.assertEqual(metrics.check("A landmark rule, amid uncertainty.", self.ALLOWED)["banned_phrases"], ["landmark", "amid"])
        bare = metrics.check("See [https://a.example/1] now.", self.ALLOWED)
        self.assertEqual((bare["links"], bare["bare_url_links"]), (0, 1))

    def test_sentences_keep_links_together(self) -> None:
        parts = common.sentences("First [claim](https://a.example/1) here. Second sentence follows! Third?")
        self.assertEqual(len(parts), 3)
        self.assertIn("https://a.example/1", parts[0])


class JudgingMathTests(unittest.TestCase):
    def test_win_rate_counts_rank_above_the_published_summary(self) -> None:
        judgments = {
            "d1": {"ranking": ["qwen3.8", "qwen3.6", "published", "gemma4"]},
            "d2": {"ranking": ["published", "qwen3.8", "gemma4", "qwen3.6"]},
        }
        self.assertEqual(report.win_rate(judgments, "qwen3.8"), (0.5, 2))
        self.assertEqual(report.win_rate(judgments, "gemma4"), (0.0, 2))

    def test_blind_key_is_stable_and_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(common, "DATA", Path(tmp)):
            names = ["gemma4", "published", "qwen3.6", "qwen3.8"]
            first = judge.blind_key("2026-10-25", names)
            second = judge.blind_key("2026-10-25", names)
            self.assertEqual(first, second)
            self.assertEqual(sorted(first.values()), names)
            self.assertEqual(sorted(first), ["A", "B", "C", "D"])


class FactSheetTests(unittest.TestCase):
    def test_fact_sheet_is_identical_between_calls_and_capped(self) -> None:
        sheet = common.fact_sheet("2026-10-25")
        self.assertEqual(sheet, common.fact_sheet("2026-10-25"))
        self.assertLessEqual(len(sheet), common.FACT_SHEET_SIZE)
        self.assertTrue(all(s["url"].startswith("http") for s in sheet))
        self.assertEqual([s["id"] for s in sheet][:2], ["S1", "S2"])


if __name__ == "__main__":
    unittest.main()
