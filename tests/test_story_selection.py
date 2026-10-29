from __future__ import annotations

import json
import unittest
from datetime import datetime
from unittest.mock import patch

import news_engine

START = datetime.fromisoformat("2026-10-28T04:30:00-04:00")
END = datetime.fromisoformat("2026-10-29T04:30:00-04:00")


def records(count: int) -> list[dict]:
    return [{"id": f"r{i}", "title": f"Waymo story {i}", "source": "Reuters", "url": f"https://example.com/{i}"} for i in range(count)]


WORDS = ("aurora kodiak waymo zoox nuro motional tesla cruise pony baidu wayve gatik plus einride embark torc "
         "volvo daimler paccar navistar isuzu hino scania iveco traton rivian lucid fisker canoo nikola hyzon "
         "joby archer wisk beta eve lilium vertical ehang volocopter skydrive supernal overair jaunt airbus "
         "boeing embraer textron bell sikorsky lockheed northrop raytheon anduril shield skydio zipline wing amtrak brightline siemens alstom hitachi talgo stadler").split()


SECTIONS = ("Autonomous Vehicles", "Robotics", "UAS / Drones", "Advanced Rail / High-Speed Rail", "Military UAS")


def cluster(record_id: str, relevant: bool = True) -> dict:
    return {"cluster_id": record_id, "article_ids": [record_id], "primary_article_id": record_id,
            "section": SECTIONS[int(record_id[1:]) % len(SECTIONS)], "relevant": relevant, "importance": 6 if relevant else 1,
            "canonical_title": f"{WORDS[int(record_id[1:])].title()} {WORDS[-1 - int(record_id[1:])]} {WORDS[(int(record_id[1:]) * 7) % 60]} expands service" if relevant else "", "summary": ""}


class FakeModel:
    """Answers each call with `judge(ids, note)` -> clusters, and records what it was asked."""

    def __init__(self, judge):
        self.judge, self.calls = judge, []

    def __call__(self, messages, api_key, model, name, schema, tokens, provider):
        body = messages[-1]["content"]
        ids = [r["id"] for r in json.loads(body.split("Build the briefing from these records:\n", 1)[1])]
        note = body.split("Build the briefing", 1)[0]
        self.calls.append((ids, note))
        return {"clusters": self.judge(ids, note), "what_to_watch": []}, {"total_tokens": 1}, 0.0


class SelectionTests(unittest.TestCase):
    def run_with(self, judge, count: int):
        fake = FakeModel(judge)
        with patch("news_engine.request_structured_output", fake):
            result, _, _ = news_engine.analyze_articles(records(count), "", "m", START, END)
        return fake, [c for c in result["clusters"] if c.get("relevant")]

    def test_records_a_batch_skipped_are_asked_about_again(self) -> None:
        # The model stops after 20 of 35 records; the other 15 get their own call.
        fake, kept = self.run_with(lambda ids, note: [cluster(i) for i in ids[:20]], 35)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(fake.calls[1][0], [f"r{i}" for i in range(20, 35)])
        self.assertEqual(len(kept), 35)

    def test_a_thin_pick_from_a_full_pool_gets_one_second_look(self) -> None:
        """2026-10-29 replay: 17 stories selected from 138 candidates; the gate needs 15 after the quality pass."""
        def judge(ids, note):
            if note:  # the second look includes what fits
                return [cluster(i) for i in ids]
            return [cluster(i, relevant=i in {"r0", "r1", "r2", "r3", "r4"}) for i in ids]

        fake, kept = self.run_with(judge, 60)
        second = [call for call in fake.calls if call[1]]
        self.assertTrue(second and "fewer than a normal edition" in second[0][1])
        self.assertNotIn("r0", [i for call in second for i in call[0]])  # kept stories are not asked again
        self.assertEqual(len(kept), 45)  # the daily story limit still applies
        self.assertEqual(len({i for c in kept for i in c["article_ids"]}), len(kept))

    def test_a_normal_pick_or_a_small_pool_is_not_second_guessed(self) -> None:
        fake, _ = self.run_with(lambda ids, note: [cluster(i) for i in ids], 60)
        self.assertFalse([call for call in fake.calls if call[1]])
        fake, _ = self.run_with(lambda ids, note: [cluster(i, relevant=i == "r0") for i in ids], 30)
        self.assertEqual(len(fake.calls), 1)

    def test_prompt_lists_current_sections_and_welcomes_on_topic_opinion(self) -> None:
        text = news_engine.prompt_messages(records(1), START, END)[0]["content"]
        self.assertIn("Advanced Rail / High-Speed Rail: Rail, including high-speed", text)
        self.assertIn("must appear in exactly one cluster", text)
        self.assertIn("opinion pieces are welcome", text)


if __name__ == "__main__":
    unittest.main()
