from __future__ import annotations

import unittest

import validation_gates as gates

# (title, url) pairs that showed up as "stories" in the October 25 edition but are not news.
NOT_ARTICLES = [
    ("Counter-Drone Tech [2026]: 4 Sensors Field-Tested, Ranked", "https://shattered.io/counter-drone-technology-first-responders-dhs-nustl-saver-2026/"),
    ("FutureFlight - Aviation International News", "https://www.ainonline.com/futureflight"),
    ("Press Releases and Announcements - FMCSA", "https://www.fmcsa.dot.gov/newsroom/press-releases"),
    ("FAA Press Releases | Federal Aviation Administration", "https://www.faa.gov/newsroom/press_releases"),
    ("Land Defense News 2026 | Army & Ground Forces Updates", "https://www.armyrecognition.com/news/army-news/2026"),
    ("Easy Access Rules for Unmanned Aircraft Systems - EASA", "https://www.easa.europa.eu/en/document-library/easy-access-rules/easy-access-rules-unmanned-aircraft-systems"),
    ("Predator Drones and Unmanned Aerial Vehicles (UAVs)", "https://www.nytimes.com/topic/subject/predator-drones"),
    ("JobyAviation: aerospace company - Reddit", "https://www.reddit.com/r/JobyAviation/new/"),
    ("EOS", "https://sg.finance.yahoo.com/quote/EOS.AX/"),
    ("Anything", "https://example.com/"),
    ("Anything", "https://example.com/news/"),
    ("Anything", "https://example.com/2026/10/"),
]

REAL_ARTICLES = [
    ("US Air Force F-22 Pilot Controls Unmanned YFQ-44 In Flight", "https://www.aero-news.net/index.cfm?do=main.textpost&id=7627FA03-CC92-4B79-B30A-DCC323A1726A"),
    ("Siemens Mobility awarded $772 million Trainset Contract", "https://metrorailtoday.com/news/siemens-mobility-awarded-772-million-trainset-contract-for-cali"),
    ("NHTSA proposes cyan lights for robotaxis", "https://electrek.co/2026/10/24/nhtsa-proposes-cyan-lights-for-robotaxis-following-mercedes-china/"),
    ("Tesla Begins Optimus Robot Production", "https://indiabusinesstrade.in/tesla-begins-optimus-robot-production.htm"),
    ("Boom Supersonic Unveils $150M Centennial Factory Expansion", "https://avi-go.com/news/articles/5afebe98839c61e4c1d7a6d05161e8dd"),
    ("FAA press releases new rules for drone pilots", "https://www.example-news.com/faa-releases-new-rules-for-drone-pilots"),
    ("Top FAA official resigns", "https://www.example-news.com/top-faa-official-resigns"),
    ("NHTSA lighting proposal", "https://www.federalregister.gov/documents/2026/10/26/2026-21794/federal-motor-vehicle-safety-standards"),
]


class NonArticleFilterTests(unittest.TestCase):
    def test_known_junk_is_flagged(self) -> None:
        for title, url in NOT_ARTICLES:
            with self.subTest(title=title):
                self.assertTrue(gates.is_non_article(title, url))

    def test_real_stories_are_kept(self) -> None:
        for title, url in REAL_ARTICLES:
            with self.subTest(title=title):
                self.assertFalse(gates.is_non_article(title, url))

    def test_candidate_pool_filter_never_drops_editor_vetted_records(self) -> None:
        pool = [
            {"title": "Press Releases - FAA", "url": "https://www.faa.gov/newsroom/press_releases", "required_include": True},
            {"title": "Press Releases - FAA", "url": "https://www.faa.gov/newsroom/press_releases"},
            {"title": "FAA finalizes drone rule", "url": "https://www.faa.gov/newsroom/faa-finalizes-drone-rule"},
        ]
        kept, removed = gates.drop_non_articles(pool)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(removed), 1)


if __name__ == "__main__":
    unittest.main()
