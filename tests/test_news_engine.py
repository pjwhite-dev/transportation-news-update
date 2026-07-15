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
    def test_prompt_presumes_supplemental_records_are_included(self) -> None:
        supplemental = record(
            origin="Supplemental daily email",
            required_include=True,
            editor_vetted=True,
        )

        instructions = news_engine.prompt_messages(
            [supplemental],
            datetime.fromisoformat("2026-07-14T04:15:00-04:00"),
            datetime.fromisoformat("2026-07-15T04:15:00-04:00"),
        )[0]["content"].casefold()

        self.assertIn("supplemental records are editor-vetted", instructions)
        self.assertIn("presume every record", instructions)
        self.assertIn("keep the required item as a distinct story", instructions)

    def test_prompt_requires_actual_article_headlines_and_executive_news_copy(
        self,
    ) -> None:
        instructions = news_engine.prompt_messages(
            [record()],
            datetime.fromisoformat("2026-07-14T04:15:00-04:00"),
            datetime.fromisoformat("2026-07-15T04:15:00-04:00"),
        )[0]["content"].casefold()

        self.assertIn("actual headline of the selected primary article", instructions)
        self.assertIn("never substitute a quotation", instructions)
        self.assertIn("standalone news briefing for a senior executive", instructions)
        self.assertIn("never mention intake methods", instructions)

    def test_ai_cannot_mark_required_supplemental_record_irrelevant(self) -> None:
        supplemental = record(
            origin="Supplemental daily email",
            required_include=True,
        )
        raw_cluster = cluster(relevant=False, exclude_reason="Low importance")

        validated = news_engine.validate_analysis(
            {
                "executive_summary": "",
                "what_to_watch": [],
                "clusters": [raw_cluster],
            },
            [supplemental],
        )

        self.assertTrue(validated["clusters"][0]["relevant"])

    def test_same_event_supplemental_url_remains_additional_coverage(self) -> None:
        automated = record(
            id="automated-1",
            title="FAA expands BVLOS drone operations",
            source="FAA",
            url="https://www.faa.gov/bvlos-action",
        )
        supplemental = record(
            id="supplemental-1",
            title="FAA expands BVLOS drone operations",
            source="Example News",
            url="https://example.com/bvlos-coverage",
            origin="Supplemental daily email",
            required_include=True,
        )
        raw_cluster = cluster(
            article_ids=["automated-1", "supplemental-1"],
            primary_article_id="automated-1",
            canonical_title="FAA expands BVLOS drone operations",
        )

        validated = news_engine.validate_analysis(
            {
                "executive_summary": "",
                "what_to_watch": [],
                "clusters": [raw_cluster],
            },
            [automated, supplemental],
        )
        story = news_engine.cluster_to_story(
            validated["clusters"][0],
            {item["id"]: item for item in (automated, supplemental)},
        )

        self.assertEqual(story["url"], automated["url"])
        self.assertIn(
            supplemental["url"],
            {item["url"] for item in story["also_covered"]},
        )

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

    def test_source_headline_overrides_pasted_prose_and_ai_paraphrase(self) -> None:
        article = record(
            title="It's creating opportunities not only for drone operators",
            original_title=(
                "FAA authorizes routine BVLOS drone operations | Example News"
            ),
            source="Example News",
            origin="Supplemental daily email",
            required_include=True,
        )
        raw_cluster = cluster(
            canonical_title="Drone rules create new opportunities",
            section="UAS and Drones",
        )
        validated = news_engine.validate_analysis(
            {
                "executive_summary": "FAA advanced drone integration.",
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
            "FAA authorizes routine BVLOS drone operations",
        )

    def test_explicit_editor_headline_override_remains_authoritative(self) -> None:
        article = record(
            title="Fetched title",
            original_title="Fetched title | Example News",
            editor_title_override="Editor-corrected actual article headline",
            source="Example News",
        )

        self.assertEqual(
            news_engine.best_record_title(article),
            "Editor-corrected actual article headline",
        )

    def test_executive_summary_removes_internal_intake_language(self) -> None:
        validated = news_engine.validate_analysis(
            {
                "executive_summary": (
                    "Required supplemental records were included. "
                    "FAA advanced a new BVLOS action affecting drone operations."
                ),
                "what_to_watch": [],
                "clusters": [cluster()],
            },
            [record()],
        )

        self.assertEqual(
            validated["executive_summary"],
            "FAA advanced a new BVLOS action affecting drone operations.",
        )

    def test_executive_summary_has_factual_fallback_if_all_copy_is_internal(
        self,
    ) -> None:
        validated = news_engine.validate_analysis(
            {
                "executive_summary": (
                    "The supplemental email satisfied link accounting."
                ),
                "what_to_watch": [],
                "clusters": [cluster()],
            },
            [record()],
        )

        summary = validated["executive_summary"].casefold()
        self.assertIn("nhtsa modernizes fmvss 108", summary)
        for marker in news_engine.EXECUTIVE_SUMMARY_PROCESS_MARKERS:
            self.assertNotIn(marker, summary)

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
