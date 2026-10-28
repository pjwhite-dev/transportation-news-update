from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock

import requests

import tracker.core as core
from regulatory_tracker import build_regulatory_tracker
from validation_gates import EASTERN, REQUIRED_RINS


def event(rin, number, kind, published, closes="", dockets=("D-1",)):
    return {
        "rin": rin,
        "document_number": number,
        "type": kind,
        "title": "t",
        "publication_date": published,
        "comments_close_on": closes,
        "docket_ids": list(dockets),
        "html_url": f"https://www.federalregister.gov/d/{number}",
        "observed_at": "2026-10-20T05:00:00-04:00",
    }


def fake_session(documents):
    session = Mock()
    response = Mock()
    response.json.return_value = {"results": documents}
    response.raise_for_status.return_value = None
    session.get.return_value = response
    return session


PROPOSED = {
    "document_number": "2026-1",
    "type": "Proposed Rule",
    "title": "T",
    "publication_date": "2026-02-01",
    "html_url": "https://www.federalregister.gov/d/2026-1",
    "comments_close_on": "2026-02-11",
    "docket_ids": ["FAA-1"],
}


class WatchListTests(unittest.TestCase):
    def test_all_ten_required_rulemakings_are_watched(self) -> None:
        entries = core.load_watch_list()
        self.assertEqual({e["rin"] for e in entries if e.get("rin")}, set(REQUIRED_RINS))
        self.assertIn("fcc-space-modernization", {e["id"] for e in entries})
        self.assertTrue(all(e["required"] for e in entries))
        for entry in entries:
            self.assertNotRegex(entry["action"], r"^(Create |Enable |Modernize |Decide )")

    def test_tracker_without_any_ledger_still_has_ten_official_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            items = core.build_tracker(
                date(2026, 10, 24),
                events_path=Path(tmp) / "e.jsonl",
                verifications_path=Path(tmp) / "v.jsonl",
            )
        self.assertEqual(len(items), 11)  # ten RINs plus the FCC docket
        for item in items:
            self.assertTrue(item["source_url"].startswith(("https://www.reginfo.gov/", "https://www.fcc.gov/")))
            self.assertEqual(item["verified_at"], "")


class StatusRuleTests(unittest.TestCase):
    AS_OF = date(2026, 10, 24)

    def test_open_comment_period_has_days_remaining(self) -> None:
        derived = core.derive_status(
            [event("R", "1", "Proposed Rule", "2026-10-01", "2026-11-03")], self.AS_OF
        )
        self.assertEqual(derived["status"], "Proposed rule (Oct. 1, 2026); comments close Nov. 3, 2026")
        self.assertEqual(derived["comment_deadline"], "2026-11-03")

    def test_closed_period_says_closed_and_never_claims_a_pending_final_rule(self) -> None:
        derived = core.derive_status(
            [event("R", "1", "Proposed Rule", "2026-02-01", "2026-02-11")], self.AS_OF
        )
        self.assertEqual(derived["status"], "Proposed rule (Feb. 1, 2026); comments closed Feb. 11, 2026")
        self.assertNotIn("pending", derived["status"])
        self.assertEqual(derived["final_rule_document"], "")

    def test_final_status_only_when_a_rule_document_exists(self) -> None:
        proposal = event("R", "1", "Proposed Rule", "2026-02-01", "2026-02-11")
        notice_only = core.derive_status(
            [event("R", "2", "Notice", "2026-09-01"), proposal], self.AS_OF
        )
        self.assertNotIn("Final", notice_only["status"])
        with_rule = core.derive_status([event("R", "9", "Rule", "2026-10-01"), proposal], self.AS_OF)
        self.assertEqual(with_rule["status"], "Final rule published Oct. 1, 2026")
        self.assertEqual(with_rule["final_rule_document"], "9")

    def test_future_dated_document_is_on_public_inspection(self) -> None:
        derived = core.derive_status(
            [event("R", "5", "Proposed Rule", "2026-10-26", "2026-11-25")], self.AS_OF
        )
        self.assertIn("public inspection", derived["status"])
        self.assertEqual(derived["comment_deadline"], "2026-11-25")

    def test_no_documents_is_stated_plainly(self) -> None:
        derived = core.derive_status([], self.AS_OF)
        self.assertEqual(
            derived["status"], "No proposed or final rule in the Federal Register yet"
        )


class LedgerAndRefreshTests(unittest.TestCase):
    NOW = datetime(2026, 10, 24, 5, 0, tzinfo=EASTERN)

    def test_refresh_appends_only_new_events_and_records_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events, verifications = Path(tmp) / "e.jsonl", Path(tmp) / "v.jsonl"
            kwargs = dict(
                now=self.NOW,
                session=fake_session([PROPOSED]),
                status_check=lambda url: 200,
                oira_fetch=lambda: None,
                discover_new=False,
                events_path=events,
                verifications_path=verifications,
            )
            first = core.refresh(**kwargs)
            core.refresh(**kwargs)
            self.assertEqual(first["errors"], [])
            self.assertEqual(len(core.read_jsonl(events)), 11)  # one per proceeding, never duplicated
            self.assertEqual(len(core.read_jsonl(verifications)), 22)
            items = core.build_tracker(
                self.NOW.date(), events_path=events, verifications_path=verifications
            )
            self.assertTrue(all(i["verified_at"] == self.NOW.isoformat() for i in items))
            self.assertEqual(items[0]["source_url"], "https://www.federalregister.gov/d/2026-1")

    def test_failed_fetch_records_no_verification_and_reports_each_error(self) -> None:
        session = Mock()
        session.get.side_effect = requests.ConnectionError("down")
        with tempfile.TemporaryDirectory() as tmp:
            report = core.refresh(
                now=self.NOW,
                session=session,
                status_check=lambda url: 200,
                oira_fetch=lambda: None,
                discover_new=False,
                events_path=Path(tmp) / "e.jsonl",
                verifications_path=Path(tmp) / "v.jsonl",
            )
            self.assertEqual(len(report["errors"]), 11)
            self.assertFalse(Path(tmp, "v.jsonl").exists())

    def test_non_200_official_link_is_not_recorded_as_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            verifications = Path(tmp) / "v.jsonl"
            report = core.refresh(
                now=self.NOW,
                session=fake_session([PROPOSED]),
                status_check=lambda url: 404,
                oira_fetch=lambda: None,
                discover_new=False,
                events_path=Path(tmp) / "e.jsonl",
                verifications_path=verifications,
            )
            self.assertTrue(report["errors"])
            self.assertEqual(core.last_verifications(verifications), {})


class CommittedLedgerTests(unittest.TestCase):
    def test_committed_tracker_has_ten_entries(self) -> None:
        items = build_regulatory_tracker(date(2026, 10, 24))
        self.assertEqual({i["rin"] for i in items if i["rin"]}, set(REQUIRED_RINS))
        self.assertIn("fcc-space-modernization", {i["key"] for i in items})


if __name__ == "__main__":
    unittest.main()
