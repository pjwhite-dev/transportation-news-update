from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import patch

import news_engine


def record(**overrides: object) -> dict:
    item = {
        "id": "article-1",
        "search_section": "Federal Actions",
        "title": "NHTSA modernizes FMVSS 108 for ADS-equipped vehicles",
        "summary": "The agency issued a new automated-driving safety action.",
        "description": "",
        "source": "NHTSA",
        "url": "https://www.nhtsa.gov/example",
        "published": "2026-07-15T03:00:00-04:00",
        "date_label": "Jul. 15, 2026",
        "origin": "Federal Register API",
    }
    item.update(overrides)
    return item


def cluster(**overrides: object) -> dict:
    item = {
        "cluster_id": "cluster-1",
        "article_ids": ["article-1"],
        "primary_article_id": "article-1",
        "section": "Federal Actions",
        "relevant": True,
        "importance": 8,
        "canonical_title": "NHTSA modernizes FMVSS 108 for ADS-equipped vehicles",
        "summary": "NHTSA issued an automated-driving safety action.",
        "is_administration_win": False,
        "win_event_within_window": False,
        "win_direct_administration_nexus": False,
        "win_concrete_american_benefit": False,
        "win_foreign_company_expansion_only": False,
        "eo_number": "",
        "eo_section": "",
        "win_explanation": "",
        "confidence": "high",
        "exclude_reason": "",
    }
    item.update(overrides)
    return item


class RelevanceAndCategorizationTests(unittest.TestCase):
    def test_unrelated_hhs_psychedelic_request_is_filtered(self) -> None:
        item = record(
            title="HHS requests information on psychedelic therapies",
            summary="The request concerns psilocybin and mental health therapy.",
            source="Department of Health and Human Services",
            origin="Google News RSS",
        )

        self.assertFalse(news_engine.automated_record_is_portfolio_relevant(item))

    def test_av_specific_federal_action_is_forced_into_av_section(self) -> None:
        analysis = {
            "executive_summary": "",
            "what_to_watch": [],
            "clusters": [cluster(section="Federal Actions")],
        }

        validated = news_engine.validate_analysis(analysis, [record()])

        self.assertEqual(
            validated["clusters"][0]["section"],
            "Autonomous Vehicles",
        )


class AdministrationWinTests(unittest.TestCase):
    def test_prompt_requires_reader_facing_plain_english_win_explanation(self) -> None:
        instructions = news_engine.prompt_messages(
            [record()],
            datetime.fromisoformat("2026-07-14T04:15:00-04:00"),
            datetime.fromisoformat("2026-07-15T04:15:00-04:00"),
        )[0]["content"].casefold()

        for prohibited_phrase in (
            "during the window",
            "the record shows",
            "clear federal procurement action",
            "direct nexus",
        ):
            with self.subTest(prohibited_phrase=prohibited_phrase):
                self.assertIn(prohibited_phrase, instructions)
        self.assertIn("published verbatim", instructions)
        self.assertIn("real-world action and result", instructions)

    def test_all_four_gates_allow_win_without_forcing_eo_citation(self) -> None:
        candidate = cluster(
            is_administration_win=True,
            win_event_within_window=True,
            win_direct_administration_nexus=True,
            win_concrete_american_benefit=True,
            win_foreign_company_expansion_only=False,
            eo_number="",
        )

        self.assertTrue(news_engine.administration_win_is_eligible(candidate))
        validated = news_engine.validate_analysis(
            {
                "executive_summary": "",
                "what_to_watch": [],
                "clusters": [candidate],
            },
            [record()],
        )
        self.assertTrue(validated["clusters"][0]["is_administration_win"])
        self.assertEqual(validated["clusters"][0]["eo_number"], "")

    def test_each_failed_gate_rejects_win(self) -> None:
        eligible = cluster(
            is_administration_win=True,
            win_event_within_window=True,
            win_direct_administration_nexus=True,
            win_concrete_american_benefit=True,
            win_foreign_company_expansion_only=False,
        )
        failing_values = {
            "win_event_within_window": False,
            "win_direct_administration_nexus": False,
            "win_concrete_american_benefit": False,
            "win_foreign_company_expansion_only": True,
        }
        for field, value in failing_values.items():
            with self.subTest(field=field):
                candidate = dict(eligible)
                candidate[field] = value
                self.assertFalse(
                    news_engine.administration_win_is_eligible(candidate)
                )


class SupplementalGuardrailTests(unittest.TestCase):
    def test_publisher_only_ai_headline_falls_back_to_specific_title(self) -> None:
        article = record(
            title="MSN",
            original_title="FAA approves expanded BVLOS drone operations",
            source="MSN",
            url="https://www.msn.com/example",
            origin="Supplemental daily email",
            required_include=True,
        )
        raw_cluster = cluster(
            canonical_title="MSN",
            section="UAS and Drones",
        )
        validated = news_engine.validate_analysis(
            {
                "executive_summary": "",
                "what_to_watch": [],
                "clusters": [raw_cluster],
            },
            [article],
        )

        story = news_engine.cluster_to_story(
            validated["clusters"][0],
            {article["id"]: article},
        )

        self.assertEqual(
            story["title"],
            "FAA approves expanded BVLOS drone operations",
        )

    @patch("news_engine.analyze_articles")
    def test_every_distinct_supplemental_url_is_counted_and_represented(
        self,
        analyze_articles,
    ) -> None:
        analyze_articles.return_value = (
            {"executive_summary": "", "what_to_watch": [], "clusters": []},
            {},
            0.0,
        )
        raw_feed = {
            "window_start": "2026-07-14T04:15:00-04:00",
            "window_end": "2026-07-15T04:15:00-04:00",
            "articles": [],
            "source_errors": [],
            "candidate_counts": {},
        }
        supplemental = [
            {
                "id": "supp-1",
                "title": "FAA expands BVLOS drone operations",
                "source": "Example News",
                "url": "https://example.com/coverage-one",
                "pasted_context": "FAA expands BVLOS drone operations.",
            },
            {
                "id": "supp-2",
                "title": "FAA expands BVLOS drone operations",
                "source": "Example News",
                "url": "https://example.com/coverage-two",
                "pasted_context": "Separate pasted link to the same reported event.",
            },
        ]

        briefing = news_engine.generate_briefing_from_records(
            raw_feed,
            supplemental,
            api_key="test-key-not-used",
        )

        self.assertEqual(briefing["supplemental_count"], 2)
        self.assertEqual(briefing["supplemental_accounted_count"], 2)
        self.assertTrue(
            {item["url"] for item in supplemental}
            <= news_engine.represented_urls(briefing["sections"])
        )


if __name__ == "__main__":
    unittest.main()
