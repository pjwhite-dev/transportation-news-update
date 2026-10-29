from __future__ import annotations

import json
import unittest
from datetime import datetime
from unittest.mock import patch

import news_engine
from news_engine import EASTERN


def cluster(cid: str, title: str, article_id: str, relevant: bool = True, importance: int = 5) -> dict:
    return {
        "cluster_id": cid,
        "article_ids": [article_id],
        "primary_article_id": article_id,
        "relevant": relevant,
        "importance": importance,
        "canonical_title": title,
    }


class BatchingTests(unittest.TestCase):
    START = datetime(2026, 10, 23, 4, 30, tzinfo=EASTERN)
    END = datetime(2026, 10, 24, 4, 30, tzinfo=EASTERN)

    def test_candidates_are_analyzed_in_small_batches_and_merged(self) -> None:
        articles = [{"id": f"a{i}", "title": f"t{i}"} for i in range(80)]
        calls: list[int] = []

        def fake(messages, api_key, model, schema_name, schema, max_tokens, provider):
            calls.append(max_tokens)
            index = len(calls) - 1
            ids = [r["id"] for r in json.loads(messages[-1]["content"].split("records:\n", 1)[1])]
            # Every record is judged (the prompt requires it); one per batch is relevant.
            return (
                {"what_to_watch": ["Shared item", f"Item {index}"],
                 "clusters": [cluster("c1", ["Zipline expands drone delivery to Dallas", "NHTSA grants Zoox exemption for robotaxis", "Army tests interceptor drones at Fort Bliss"][index], ids[0])]
                 + [cluster(f"x{i}", "", i, relevant=False, importance=1) for i in ids[1:]]},
                {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "_retries": 1},
                0.0,
            )

        with patch.object(news_engine, "request_structured_output", fake), patch.object(news_engine, "THIN_SELECTION_FLOOR", 0):
            merged, usage, cost = news_engine.analyze_articles(
                articles, "", "m", self.START, self.END, None
            )
        self.assertEqual(len(calls), 3)  # 80 candidates / batches of 35
        self.assertTrue(all(tokens == news_engine.ANALYSIS_BATCH_OUTPUT_TOKENS for tokens in calls))
        relevant = [c for c in merged["clusters"] if c["relevant"]]
        self.assertEqual(len(relevant), 3)
        self.assertEqual(len({c["cluster_id"] for c in relevant}), 3)
        self.assertEqual(merged["what_to_watch"], ["Shared item", "Item 0", "Item 1", "Item 2"])
        self.assertEqual(usage["total_tokens"], 45)
        self.assertEqual(usage["_retries"], 3)
        self.assertEqual(cost, 0.0)

    def test_same_event_across_batches_is_folded_into_one_story(self) -> None:
        merged = news_engine.merge_duplicate_clusters(
            [
                cluster("b0-1", "Hegseth, Guyanese President Establish Enhanced Military Partnership", "a1", importance=4),
                cluster("b1-1", "Hegseth, President of Guyana Establish Enhanced Military Partnership", "a2", importance=7),
                cluster("b1-2", "FAA finalizes BVLOS drone rule", "a3"),
                cluster("b1-3", "Hegseth, President of Guyana Establish Enhanced Military Partnership", "a4", relevant=False),
            ]
        )
        self.assertEqual(len(merged), 3)
        self.assertEqual(merged[0]["article_ids"], ["a1", "a2"])
        self.assertEqual(merged[0]["importance"], 7)
        self.assertFalse(merged[2]["relevant"])  # irrelevant clusters are never folded in


class DailyCutTests(unittest.TestCase):
    def _clusters(self):
        out = []
        for index in range(60):
            c = cluster(f"c{index}", f"Story {index}", f"a{index}", importance=3 + index % 7)
            c["section"] = "Military UAS" if index % 2 else "UAS / Drones"
            out.append(c)
        return out

    def test_daily_and_section_limits_apply_and_cuts_are_recorded(self) -> None:
        clusters = news_engine.select_top_clusters(self._clusters(), set())
        kept = [c for c in clusters if c["relevant"]]
        self.assertLessEqual(len(kept), news_engine.DAILY_STORY_LIMIT)
        for section in ("Military UAS", "UAS / Drones"):
            self.assertLessEqual(sum(c["section"] == section for c in kept), news_engine.SECTION_STORY_LIMIT)
        self.assertEqual(len(kept), 24)
        self.assertGreaterEqual(min(c["importance"] for c in kept), 3)
        cut = [c for c in clusters if not c["relevant"]]
        self.assertTrue(all("daily cut" in c["exclude_reason"] for c in cut))

    def test_low_importance_is_cut_and_required_articles_are_never_cut(self) -> None:
        low = cluster("low", "Minor event", "a-low", importance=1)
        low["section"] = "Military UAS"
        vetted = cluster("vet", "Editor pick", "a-vet", importance=1)
        vetted["section"] = "Military UAS"
        clusters = news_engine.select_top_clusters([low, vetted], {"a-vet"})
        self.assertFalse(clusters[0]["relevant"])
        self.assertTrue(clusters[1]["relevant"])


if __name__ == "__main__":
    unittest.main()
