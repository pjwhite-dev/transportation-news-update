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
        "section": "Federal Policy & Implementation",
        "relevant": True,
        "importance": 8,
        "canonical_title": "NHTSA modernizes FMVSS 108 for ADS-equipped vehicles",
        "summary": "NHTSA issued an automated-driving safety action.",
        "innovative_uas_use": "",
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
            "clusters": [cluster(section="UAS / Drones")],
        }

        validated = news_engine.validate_analysis(analysis, [article])

        self.assertIn("Military UAS", news_engine.TOPIC_SECTIONS)
        self.assertEqual(validated["clusters"][0]["section"], "Military UAS")

    def test_defense_counter_uas_technology_is_forced_into_cuas_section(self) -> None:
        article = record(
            title="U.S. Army awards contract for new counter-UAS interceptors",
            summary=(
                "The service ordered detection and mitigation systems to protect "
                "domestic installations."
            ),
            source="U.S. Army",
            origin="Supplemental daily email",
            required_include=True,
        )

        validated = news_engine.validate_analysis(
            {
                "what_to_watch": [],
                "clusters": [cluster(section="Military UAS")],
            },
            [article],
        )

        self.assertEqual(
            validated["clusters"][0]["section"],
            "Counter-UAS / Airspace Security",
        )

    def test_counter_uas_use_in_ukraine_is_international_security(self) -> None:
        article = record(
            title="Ukraine deploys counter-UAS systems against Russian attacks",
            summary=(
                "The battlefield systems intercepted drones during active combat."
            ),
            source="Example Defense News",
            origin="Supplemental daily email",
            required_include=True,
        )

        self.assertEqual(news_engine.infer_section(article), "International Security")

    def test_pentagon_drone_defense_test_is_cuas_without_acronym(self) -> None:
        article = record(
            title="Pentagon tests a new drone defense laser",
            summary="The system detects and defeats drones around U.S. installations.",
            source="Department of Defense",
            origin="Google News RSS",
        )

        self.assertEqual(
            news_engine.infer_section(article),
            "Counter-UAS / Airspace Security",
        )

    def test_innovative_civil_uas_use_gets_short_deterministic_label(self) -> None:
        article = record(
            search_section="UAS and Drones",
            title="Drones begin delivering blood to remote hospitals",
            summary="The service transports medical supplies to rural communities.",
            source="Example Aviation News",
            origin="Google News RSS",
        )

        self.assertEqual(news_engine.infer_section(article), "UAS / Drones")
        self.assertEqual(
            news_engine.infer_innovative_uas_use(article),
            "Delivering critical medical supplies",
        )

    def test_drone_memorial_gets_innovative_use_label(self) -> None:
        article = record(
            search_section="UAS and Drones",
            origin="Supplemental daily email",
            source="Fast Company",
            url="https://example.com/drone-memorial",
            title="It took 2,977 drones to create this 9/11 memorial",
            summary="A coordinated aerial display commemorated those who died.",
        )

        self.assertEqual(
            news_engine.infer_innovative_uas_use(article),
            "Creating large-scale aerial memorials",
        )

    def test_drone_light_show_gets_innovative_use_label(self) -> None:
        article = record(
            search_section="UAS and Drones",
            origin="Supplemental daily email",
            source="DroneDJ",
            url="https://example.com/drone-light-show",
            title="Drone light shows face new equipment restrictions",
            summary="Purpose-built aerial entertainment drones create displays.",
        )

        self.assertEqual(
            news_engine.infer_innovative_uas_use(article),
            "Producing coordinated aerial light shows",
        )

    def test_incomplete_metadata_summary_is_omitted(self) -> None:
        summary = (
            "Industry leaders exchanged ideas as uncrewed aviation rapidly moves "
            "towards the"
        )

        self.assertEqual(
            news_engine.sanitize_story_summary(
                "Commercial UAV Expo and the Internationalization of Uncrewed Aviation",
                summary,
            ),
            "",
        )

    def test_repeated_headline_fragment_is_removed_from_summary(self) -> None:
        title = "It took 2,977 drones to create this 9/11 memorial show"
        summary = (
            "Finding a suitable launch location was one of the biggest challenges. "
            f"Fast Company: {title}"
        )

        self.assertEqual(
            news_engine.sanitize_story_summary(title, summary),
            "Finding a suitable launch location was one of the biggest challenges.",
        )

    def test_innovative_uas_label_is_removed_from_cuas_story(self) -> None:
        article = record(
            title="FAA tests counter-UAS detection at an airport",
            summary="The test evaluates drone detection technology.",
            source="FAA",
            origin="Supplemental daily email",
            required_include=True,
        )
        validated = news_engine.validate_analysis(
            {
                "what_to_watch": [],
                "clusters": [
                    cluster(
                        section="UAS / Drones",
                        innovative_uas_use="Detecting unsafe airport drones",
                    )
                ],
            },
            [article],
        )

        self.assertEqual(
            validated["clusters"][0]["section"],
            "Counter-UAS / Airspace Security",
        )
        self.assertEqual(validated["clusters"][0]["innovative_uas_use"], "")

    def test_ukraine_conflict_story_is_forced_out_of_uas(self) -> None:
        # Regression: Ukraine, Russia and Iran were not international markers, so
        # foreign war reporting was filed as Military UAS in the Oct 20-29 editions.
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
                "clusters": [cluster(section="UAS / Drones")],
            },
            [article],
        )

        self.assertEqual(validated["clusters"][0]["section"], "International Security")

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
                "clusters": [cluster(section="UAS / Drones")],
            },
            [article],
        )

        self.assertEqual(validated["clusters"][0]["section"], "International Security")

    def test_civilian_ukraine_rail_story_is_international_not_military(self) -> None:
        article = record(
            title="New passenger rail route links Germany, Poland, and Ukraine",
            summary="The cross-border service will add overnight passenger trains.",
            source="Example Travel News",
            origin="Google News RSS",
            search_section="Other Advanced Transportation",
        )

        self.assertTrue(news_engine.record_is_international(article))
        self.assertFalse(news_engine.record_is_military(article))
        self.assertEqual(
            news_engine.infer_section(article),
            "Advanced Rail / High-Speed Rail",
        )

    def test_foreign_robotaxi_story_is_international_and_stays_in_av(self) -> None:
        article = record(
            title="Madrid approves a new commercial robotaxi service",
            summary="Spanish regulators cleared the driverless deployment.",
            source="Example News",
            origin="Google News RSS",
            search_section="Autonomous Vehicles",
        )

        self.assertTrue(news_engine.record_is_international(article))
        self.assertEqual(news_engine.infer_section(article), "Autonomous Vehicles")

    def test_latin_america_is_not_mistaken_for_the_united_states(self) -> None:
        article = record(
            title="Brazil approves a new high-speed rail corridor",
            summary="The project would be the fastest passenger line in Latin America.",
            source="Example News",
            origin="Google News RSS",
            search_section="Other Advanced Transportation",
        )

        self.assertTrue(news_engine.record_is_international(article))
        self.assertEqual(
            news_engine.infer_section(article),
            "Advanced Rail / High-Speed Rail",
        )

    def test_us_robotaxi_story_remains_autonomous_vehicles(self) -> None:
        article = record(
            title="California approves Waymo robotaxi expansion",
            summary="The U.S. deployment will add driverless service in two cities.",
            source="Example News",
            origin="Google News RSS",
            search_section="Autonomous Vehicles",
        )

        self.assertEqual(news_engine.infer_section(article), "Autonomous Vehicles")

    def test_credible_sector_candidates_are_restored_if_ai_omits_them(self) -> None:
        articles = [
            record(
                id="av-1",
                search_section="Autonomous Vehicles",
                title="California approves Waymo robotaxi expansion",
                summary="The U.S. deployment will add driverless service.",
                source="Example News",
                origin="Google News RSS",
            ),
            record(
                id="rail-1",
                search_section="Other Advanced Transportation",
                title="U.S. Department of Transportation backs high-speed rail test",
                summary="The new test will evaluate advanced passenger rail technology.",
                source="U.S. Department of Transportation",
                origin="Google News RSS",
            ),
            record(
                id="international-1",
                search_section="Other Advanced Transportation",
                title="India launches its first hydrogen train",
                summary="The passenger service uses hydrogen fuel cell technology.",
                source="Example News",
                origin="Google News RSS",
            ),
        ]

        validated = news_engine.validate_analysis(
            {"what_to_watch": [], "clusters": []},
            articles,
        )
        relevant_sections = {
            item["section"]
            for item in validated["clusters"]
            if item["relevant"]
        }

        self.assertEqual(
            relevant_sections,
            {"Autonomous Vehicles", "Advanced Rail / High-Speed Rail"},
        )
        self.assertEqual(
            set(validated["coverage_floor_sections"]),
            {"Autonomous Vehicles", "Advanced Rail / High-Speed Rail"},
        )
        restored = {
            article_id
            for item in validated["clusters"]
            for article_id in item["article_ids"]
        }
        # The floor restores U.S. sector coverage; foreign stories are not floor picks.
        self.assertNotIn("international-1", restored)

    def test_market_and_stock_noise_does_not_fill_empty_sections(self) -> None:
        articles = [
            record(
                id="noise-av",
                search_section="Autonomous Vehicles",
                title="Autonomous Vehicle Chassis Market Forecast Through 2035",
                source="Market Reports",
                origin="Google News RSS",
            ),
            record(
                id="noise-rail",
                search_section="Other Advanced Transportation",
                title="High-Speed Rail Fasteners Market Insights and CAGR",
                source="Market Reports",
                origin="Google News RSS",
            ),
        ]

        validated = news_engine.validate_analysis(
            {"what_to_watch": [], "clusters": []},
            articles,
        )

        self.assertEqual(validated["coverage_floor_sections"], [])
        self.assertEqual(validated["clusters"], [])

    def test_search_bucket_alone_does_not_fill_an_empty_sector(self) -> None:
        articles = [
            record(
                id="query-collision",
                search_section="Autonomous Vehicles",
                title="Amazon executive discusses quantum computing",
                source="Stock News",
                origin="Google News RSS",
            ),
        ]

        validated = news_engine.validate_analysis(
            {"what_to_watch": [], "clusters": []},
            articles,
        )

        self.assertEqual(validated["coverage_floor_sections"], [])
        self.assertEqual(validated["clusters"], [])

class AdministrationWinRetirementTests(unittest.TestCase):
    def test_model_win_flags_and_eo_citation_are_cleared(self) -> None:
        # Administration Wins were retired: executive orders are policy context only,
        # so a model that still sets every legacy win gate gets no win and no citation.
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
            {"what_to_watch": [], "clusters": [raw_cluster]},
            [article],
        )
        checked = validated["clusters"][0]
        story = news_engine.cluster_to_story(checked, {article["id"]: article})

        self.assertFalse(checked["is_administration_win"])
        self.assertEqual(validated["recognized_administration_win_ids"], [])
        for field in ("eo_number", "eo_section", "win_explanation"):
            with self.subTest(field=field):
                self.assertEqual(checked[field], "")
        self.assertEqual(story["eo_section_summary"], "")
        self.assertNotIn("Trump Administration Wins", news_engine.SECTION_ORDER)


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
        self.assertIn("minimum sector coverage", instructions)
        self.assertIn("strongest new autonomous vehicles", instructions)
        self.assertIn("international story", instructions)
        self.assertIn("polished paragraph for a senior executive", summary_instructions)
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
            section="UAS / Drones",
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
            section="UAS / Drones",
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

    def test_executive_summary_removes_exposed_url_citations(self) -> None:
        summary = news_engine.sanitize_executive_summary_citations(
            "FAA completed a test [https://news.google.com/rss/articles/very-long]. "
            "Rail service expanded <https://example.com/rail>. "
            "A [labeled source](https://example.com/source) remains readable."
        )

        self.assertEqual(
            summary,
            "FAA completed a test. Rail service expanded. "
            "A [labeled source](https://example.com/source) remains readable.",
        )

    def test_final_summary_prompt_contains_only_compiled_reader_fields(self) -> None:
        story = {
            "id": "story-1",
            "title": "Ukraine deploys long-range drones in overnight strikes",
            "summary": "Ukrainian forces used unmanned aircraft against military targets.",
            "source": "Example News",
            "date_label": "Jul. 15, 2026",
            "section": "International Security",
            "is_administration_win": True,
            "win_explanation": "Internal Win explanation.",
            "required_include": True,
        }
        sections = {section: [] for section in news_engine.SECTION_ORDER}
        sections["International Security"] = [story]

        prompt = news_engine.executive_summary_messages(
            sections,
            [],
            [],
            datetime.fromisoformat("2026-07-14T04:15:00-04:00"),
            datetime.fromisoformat("2026-07-15T04:15:00-04:00"),
        )[1]["content"]

        self.assertIn(story["title"], prompt)
        self.assertIn('"topic": "International Security"', prompt)
        for internal_field in (
            "is_administration_win",
            "win_explanation",
            "required_include",
        ):
            with self.subTest(internal_field=internal_field):
                self.assertNotIn(internal_field, prompt)

    def test_executive_summary_removes_win_eligibility_commentary(self) -> None:
        sections = {section: [] for section in news_engine.SECTION_ORDER}
        sections["Military UAS"] = [
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
            section="UAS / Drones",
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

    def test_executive_summary_fails_instead_of_using_template_text_if_all_copy_is_internal(
        self,
    ) -> None:
        sections = {section: [] for section in news_engine.SECTION_ORDER}
        sections["Top Developments"] = [
            {"title": "NHTSA modernizes FMVSS 108 for ADS-equipped vehicles"}
        ]
        with self.assertRaisesRegex(ValueError, "refusing to substitute template text"):
            news_engine.sanitize_compiled_executive_summary(
                "The supplemental email satisfied link accounting.",
                sections,
            )

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
                sections["Autonomous Vehicles"][0]["title"],
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


class ReaderFacingCopyTests(unittest.TestCase):
    def test_story_summary_sanitizer_removes_editorial_process_sentences(self) -> None:
        summary = (
            "Russian forces approached Izium in eastern Ukraine. "
            "The supplied record does not show a U.S. policy action. "
            "This belongs in Military rather than UAS."
        )

        self.assertEqual(
            news_engine.sanitize_story_summary("Russia draws near Izium", summary),
            "Russian forces approached Izium in eastern Ukraine.",
        )

    def test_placeholder_title_does_not_override_pasted_headline(self) -> None:
        item = record(
            title="Headline unavailable — review this link",
            pasted_headline=(
                "NYT: As Russia Again Draws Near, a Frontline City Fears "
                "Obliteration This Time"
            ),
        )

        self.assertEqual(
            news_engine.best_record_title(item),
            (
                "NYT: As Russia Again Draws Near, a Frontline City Fears "
                "Obliteration This Time"
            ),
        )


if __name__ == "__main__":
    unittest.main()
