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

    def test_military_story_is_forced_into_military_section(self) -> None:
        article = record(
            title="U.S. Army awards contract for autonomous reconnaissance drones",
            summary="The military program will deploy new unmanned aircraft.",
            source="U.S. Army",
            origin="Supplemental daily email",
            required_include=True,
        )
        analysis = {
            "executive_summary": "The Army expanded autonomous aviation capability.",
            "what_to_watch": [],
            "clusters": [cluster(section="UAS and Drones")],
        }

        validated = news_engine.validate_analysis(analysis, [article])

        self.assertIn("Military", news_engine.TOPIC_SECTIONS)
        self.assertEqual(validated["clusters"][0]["section"], "Military")

    def test_ukraine_conflict_story_is_forced_out_of_uas(self) -> None:
        article = record(
            title="Ukraine deploys long-range drones in overnight strikes",
            summary="Ukrainian forces used unmanned aircraft against military targets.",
            source="Example News",
            origin="Supplemental daily email",
            required_include=True,
        )
        validated = news_engine.validate_analysis(
            {
                "what_to_watch": [],
                "clusters": [cluster(section="UAS and Drones")],
            },
            [article],
        )

        self.assertEqual(validated["clusters"][0]["section"], "Military")

    def test_russian_ship_attack_is_forced_out_of_uas(self) -> None:
        article = record(
            title="Russian ships hit in unmanned naval attack",
            summary="Unmanned vessels damaged two warships during combat operations.",
            source="Example News",
            origin="Supplemental daily email",
            required_include=True,
        )
        validated = news_engine.validate_analysis(
            {
                "what_to_watch": [],
                "clusters": [cluster(section="UAS and Drones")],
            },
            [article],
        )

        self.assertEqual(validated["clusters"][0]["section"], "Military")

    def test_civilian_ukraine_rail_story_is_not_forced_into_military(self) -> None:
        article = record(
            title="New passenger rail route links Germany, Poland, and Ukraine",
            summary="The cross-border service will add overnight passenger trains.",
            source="Example Travel News",
            origin="Google News RSS",
            search_section="Other Advanced Transportation",
        )

        self.assertEqual(
            news_engine.infer_section(article),
            "Other Advanced Transportation",
        )

    def test_win_toggle_moves_story_into_and_out_of_win_section(self) -> None:
        story = {
            "id": "military-1",
            "section": "Military",
            "importance": 5,
            "published": "2026-07-15T03:00:00-04:00",
            "is_administration_win": True,
        }

        checked = news_engine.arrange_sections([story])
        self.assertEqual(checked["Trump Administration Wins"], [story])
        self.assertEqual(checked["Military"], [])

        story["is_administration_win"] = False
        unchecked = news_engine.arrange_sections([story])
        self.assertEqual(unchecked["Trump Administration Wins"], [])
        self.assertEqual(unchecked["Military"], [story])


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
        summary_instructions = news_engine.executive_summary_messages(
            {section: [] for section in news_engine.SECTION_ORDER},
            [],
            [],
            datetime.fromisoformat("2026-07-14T04:15:00-04:00"),
            datetime.fromisoformat("2026-07-15T04:15:00-04:00"),
        )[0]["content"].casefold()

        self.assertIn("actual headline of the selected primary article", instructions)
        self.assertIn("never substitute a quotation", instructions)
        self.assertIn("do not write an executive summary in this pass", instructions)
        self.assertIn("in military", instructions)
        self.assertIn("war in ukraine", instructions)
        self.assertIn("standalone briefing for a senior executive", summary_instructions)
        self.assertIn("final step", summary_instructions)
        self.assertIn("do not discuss whether any item is or is not", summary_instructions)

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
        sections = {section: [] for section in news_engine.SECTION_ORDER}
        sections["Top Developments"] = [
            {"title": "FAA advanced a new BVLOS action"}
        ]
        summary = news_engine.sanitize_compiled_executive_summary(
            "Required supplemental records were included. "
            "FAA advanced a new BVLOS action affecting drone operations.",
            sections,
        )

        self.assertEqual(
            summary,
            "FAA advanced a new BVLOS action affecting drone operations.",
        )

    def test_final_summary_prompt_contains_only_compiled_reader_fields(self) -> None:
        story = {
            "id": "story-1",
            "title": "Ukraine deploys long-range drones in overnight strikes",
            "summary": "Ukrainian forces used unmanned aircraft against military targets.",
            "source": "Example News",
            "date_label": "Jul. 15, 2026",
            "section": "Military",
            "is_administration_win": True,
            "win_explanation": "Internal Win explanation.",
            "required_include": True,
        }
        sections = {section: [] for section in news_engine.SECTION_ORDER}
        sections["Trump Administration Wins"] = [story]

        prompt = news_engine.executive_summary_messages(
            sections,
            [],
            [],
            datetime.fromisoformat("2026-07-14T04:15:00-04:00"),
            datetime.fromisoformat("2026-07-15T04:15:00-04:00"),
        )[1]["content"]

        self.assertIn(story["title"], prompt)
        self.assertIn('"topic": "Military"', prompt)
        for internal_field in (
            "is_administration_win",
            "win_explanation",
            "required_include",
            "Trump Administration Wins",
        ):
            with self.subTest(internal_field=internal_field):
                self.assertNotIn(internal_field, prompt)

    def test_executive_summary_removes_win_eligibility_commentary(self) -> None:
        sections = {section: [] for section in news_engine.SECTION_ORDER}
        sections["Military"] = [
            {"title": "The Army expanded its autonomous aircraft program"}
        ]
        summary = news_engine.sanitize_compiled_executive_summary(
            "None of the stories qualifies as an Administration Win. "
            "The Army expanded its autonomous aircraft program.",
            sections,
        )

        self.assertEqual(
            summary,
            "The Army expanded its autonomous aircraft program.",
        )

    def test_duplicate_story_summary_is_omitted(self) -> None:
        article = record(title="FAA authorizes routine BVLOS operations")
        raw_cluster = cluster(
            canonical_title="Ignored AI title",
            summary="FAA authorizes routine BVLOS operations.",
            section="UAS and Drones",
        )
        validated = news_engine.validate_analysis(
            {
                "executive_summary": "FAA advanced routine drone operations.",
                "what_to_watch": [],
                "clusters": [raw_cluster],
            },
            [article],
        )

        story = news_engine.cluster_to_story(
            validated["clusters"][0],
            {article["id"]: article},
        )

        self.assertEqual(story["summary"], "")

    def test_eo_section_is_normalized_and_gets_plain_english_summary(self) -> None:
        article = record(title="FAA expands domestic UAS commercialization")
        raw_cluster = cluster(
            is_administration_win=True,
            win_event_within_window=True,
            win_direct_administration_nexus=True,
            win_concrete_american_benefit=True,
            win_foreign_company_expansion_only=False,
            eo_number="EO 14307",
            eo_section="Sec. 3",
            win_explanation="FAA expanded a domestic UAS commercialization program.",
        )
        validated = news_engine.validate_analysis(
            {
                "executive_summary": "FAA expanded domestic UAS capability.",
                "what_to_watch": [],
                "clusters": [raw_cluster],
            },
            [article],
        )
        story = news_engine.cluster_to_story(
            validated["clusters"][0],
            {article["id"]: article},
        )

        self.assertEqual(story["eo_section"], "Section 3")
        self.assertEqual(
            story["eo_section_summary"],
            "advancing domestic commercialization of UAS technologies at scale",
        )

    def test_executive_summary_has_factual_fallback_if_all_copy_is_internal(
        self,
    ) -> None:
        sections = {section: [] for section in news_engine.SECTION_ORDER}
        sections["Top Developments"] = [
            {"title": "NHTSA modernizes FMVSS 108 for ADS-equipped vehicles"}
        ]
        summary = news_engine.sanitize_compiled_executive_summary(
            "The supplemental email satisfied link accounting.",
            sections,
        )

        summary = summary.casefold()
        self.assertIn("nhtsa modernizes fmvss 108", summary)
        for marker in news_engine.EXECUTIVE_SUMMARY_PROCESS_MARKERS:
            self.assertNotIn(marker, summary)

    @patch("news_engine.generate_final_executive_summary")
    @patch("news_engine.analyze_articles")
    def test_every_distinct_supplemental_url_is_counted_and_represented(
        self,
        analyze_articles,
        generate_final_executive_summary,
    ) -> None:
        analyze_articles.return_value = (
            {"what_to_watch": [], "clusters": []},
            {},
            0.0,
        )
        generate_final_executive_summary.return_value = ("Final summary.", {}, 0.0)
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

    @patch("news_engine.generate_final_executive_summary")
    @patch("news_engine.analyze_articles")
    def test_executive_summary_runs_after_compiled_sections(
        self,
        analyze_articles,
        generate_final_executive_summary,
    ) -> None:
        call_order = []
        self.assertNotIn(
            "executive_summary",
            news_engine.analysis_schema()["properties"],
        )

        def analyze(*args, **kwargs):
            call_order.append("analysis")
            return (
                {"what_to_watch": ["Watch the next agency action."], "clusters": [cluster()]},
                {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                0.2,
            )

        def summarize(sections, what_to_watch, tracker, *args, **kwargs):
            call_order.append("summary")
            self.assertEqual(
                sections["Top Developments"][0]["title"],
                "NHTSA modernizes FMVSS 108 for ADS-equipped vehicles",
            )
            self.assertEqual(what_to_watch, ["Watch the next agency action."])
            self.assertTrue(tracker)
            return (
                "NHTSA advanced automated-driving safety policy.",
                {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
                0.1,
            )

        analyze_articles.side_effect = analyze
        generate_final_executive_summary.side_effect = summarize
        raw_feed = {
            "window_start": "2026-07-14T04:15:00-04:00",
            "window_end": "2026-07-15T04:15:00-04:00",
            "articles": [record()],
            "source_errors": [],
            "candidate_counts": {},
        }

        briefing = news_engine.generate_briefing_from_records(
            raw_feed,
            [],
            api_key="test-key-not-used",
        )

        self.assertEqual(call_order, ["analysis", "summary"])
        self.assertEqual(
            briefing["executive_summary"],
            "NHTSA advanced automated-driving safety policy.",
        )
        self.assertEqual(
            briefing["usage"],
            {"input_tokens": 13, "output_tokens": 3, "total_tokens": 16},
        )
        self.assertAlmostEqual(briefing["estimated_cost"], 0.3)


if __name__ == "__main__":
    unittest.main()
