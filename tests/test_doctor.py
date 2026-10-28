from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import doctor


class ClassifyTests(unittest.TestCase):
    def test_known_signatures_get_a_plain_recovery(self) -> None:
        for text, cause in [
            ("throw Refusing production run because the repository is not clean: M x", "dirty_tree"),
            ("Ollama health check failed.", "ollama_down"),
            ("git push failed; the local commit is preserved", "push_failed"),
            ("the live deployment was not verified within five minutes", "deploy_unverified"),
            ("Another edition run is active; exiting.", "running"),
        ]:
            found, recovery = doctor.classify(text)
            self.assertEqual(found, cause)
            self.assertTrue(recovery)

    def test_gate_failures_and_tracebacks_go_to_the_model_and_plain_noise_is_unknown(self) -> None:
        self.assertEqual(doctor.classify("ValueError: Complete briefing validation failed:\n- links: x")[0], "gate_failure")
        self.assertEqual(doctor.classify("Traceback (most recent call last):\n  File x")[0], "code_error")
        self.assertEqual(doctor.classify("nothing recognisable")[0], "unknown")


class EditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        (self.root / "tests").mkdir()
        (self.root / "thing.py").write_text("x = 1\ny = 2\n", encoding="utf-8")

    def test_unique_edit_applies(self) -> None:
        changed = doctor.apply_edits(self.root, [{"file": "thing.py", "old": "x = 1", "new": "x = 3"}])
        self.assertEqual(changed, ["thing.py"])
        self.assertEqual((self.root / "thing.py").read_text(encoding="utf-8"), "x = 3\ny = 2\n")

    def test_missing_ambiguous_or_noop_edits_are_rejected_without_changes(self) -> None:
        (self.root / "thing.py").write_text("a\na\n", encoding="utf-8")
        for old in ("a", "zzz"):
            with self.assertRaises(ValueError):
                doctor.apply_edits(self.root, [{"file": "thing.py", "old": old, "new": "b"}])
        with self.assertRaises(ValueError):
            doctor.apply_edits(self.root, [{"file": "thing.py", "old": "a\na", "new": "a\na"}])
        self.assertEqual((self.root / "thing.py").read_text(encoding="utf-8"), "a\na\n")

    def test_only_code_files_may_be_edited(self) -> None:
        for name in ("data/latest_briefing.json", ".github/workflows/publish-news-site.yml", "alerts.py", "doctor.py",
                     "../outside.py", "tests/regression/known_bad/x.json", "scripts/sub/x.ps1"):
            self.assertFalse(doctor.allowed_edit({"file": name}), name)
        for name in ("validation_gates.py", "tests/test_x.py", "tracker/core.py", "scripts/run_local_pipeline.ps1",
                     "scripts/ensure_edition.ps1", "config/editorial.yaml", "edition_preflight.py"):
            self.assertTrue(doctor.allowed_edit({"file": name}), name)

    def test_script_failure_is_classified_and_read_by_the_doctor(self) -> None:
        log = ("Host Application: powershell.exe -NoProfile -File C:/newsroom/scripts/run_local_pipeline.ps1\n"
               '>> TerminatingError(git.exe): "error: pathspec \'tracker/discovered.yaml\' did not match"')
        self.assertEqual(doctor.classify(log)[0], "script_error")

    def test_rerun_is_claimed_once_per_day(self) -> None:
        self.assertTrue(doctor.claim_rerun(self.root, "2026-10-28"))
        self.assertFalse(doctor.claim_rerun(self.root, "2026-10-28"))
        self.assertTrue(doctor.claim_rerun(self.root, "2026-10-29"))

    def test_a_failed_second_edit_leaves_the_first_unapplied(self) -> None:
        with self.assertRaises(ValueError):
            doctor.apply_edits(self.root, [
                {"file": "thing.py", "old": "x = 1", "new": "x = 9"},
                {"file": "thing.py", "old": "nope", "new": "q"},
            ])
        self.assertEqual((self.root / "thing.py").read_text(encoding="utf-8"), "x = 1\ny = 2\n")


class EvidenceTests(unittest.TestCase):
    def test_traceback_files_stay_inside_the_repo(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "mod.py").write_text("pass\n", encoding="utf-8")
        log = f'File "{root / "mod.py"}", line 3\nFile "C:\\Windows\\other.py", line 4'
        self.assertEqual([p.as_posix() for p in doctor.traceback_files(log, root)], ["mod.py"])

    def test_log_tail_is_redacted(self) -> None:
        root = Path(tempfile.mkdtemp())
        logs = root / ".runtime" / "logs"
        logs.mkdir(parents=True)
        (logs / "edition-2026-10-27-043002.log").write_text("token sk-ABCDEFGHIJKLMNOPQRSTUV leaked\n", encoding="utf-8")
        self.assertNotIn("sk-ABCDEFGHIJKLMNOPQRSTUV", doctor.latest_log(root, "2026-10-27"))

    def test_live_edition_reads_the_marker(self) -> None:
        class Page:
            text = '<meta name="edition-id" content="2026-10-27">'

        class Session:
            def get(self, url, params=None, timeout=None):
                return Page()

        self.assertEqual(doctor.live_edition(Session()), "2026-10-27")


class IncidentTests(unittest.TestCase):
    def test_key_ignores_numbers_and_urls_but_not_the_kind_of_failure(self) -> None:
        a = doctor.incident_key("gate_failure", "ValueError: x\n- links: Military[1] returned HTTP None: https://a.com/1")
        b = doctor.incident_key("gate_failure", "ValueError: x\n- links: Military[7] returned HTTP None: https://b.org/99")
        c = doctor.incident_key("gate_failure", "ValueError: x\n- freshness: Edition date is not today")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_repeats_are_counted_from_the_ledger(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.assertEqual(doctor.record_incident(root, "2026-10-27", "k", None), 0)
        self.assertEqual(doctor.record_incident(root, "2026-10-29", "k", "doctor/2026-10-29"), 1)
        self.assertEqual(doctor.record_incident(root, "2026-10-30", "other", None), 0)


class BranchGateTests(unittest.TestCase):
    def test_a_fix_without_a_regression_test_is_not_kept(self) -> None:
        root = Path(tempfile.mkdtemp())
        branch, note = doctor.prepare_branch(root, "2026-10-27", [{"file": "a.py", "old": "x", "new": "y"}], "", "python")
        self.assertIsNone(branch)
        self.assertIn("regression test", note)


class GateContextTests(unittest.TestCase):
    def test_gate_failure_shows_repair_rules_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "validation_gates.py").write_text("x = 1\n", encoding="utf-8")
            files = doctor.context_files("gate_failure", "no traceback here", repo)
            self.assertEqual([f.as_posix() for f in files], ["validation_gates.py"])
            self.assertEqual(doctor.context_files("code_error", "no traceback here", repo), [])

    def test_prompt_treats_single_item_gate_failures_as_fixable(self) -> None:
        self.assertIn("ONE bad item", doctor.SYSTEM)
        self.assertIn("exactly as they are", doctor.SYSTEM)

    def test_empty_code_fix_is_retried_until_it_has_edits(self) -> None:
        empty = {"kind": "code_fix", "cause": "c", "next_step": "n", "edits": [], "regression_test": ""}
        good = {**empty, "edits": [{"file": "a.py", "old": "x", "new": "y"}], "regression_test": "import unittest"}
        replies = [empty, empty, good]

        class Provider:
            def generate_structured(self, *args, **kwargs):
                class Result:
                    value = replies.pop(0)
                return Result()

        with tempfile.TemporaryDirectory() as tmp:
            verdict = doctor.diagnose("Complete briefing validation failed", Path(tmp), [], Provider())
        self.assertEqual(verdict, good)
        self.assertEqual(replies, [])


if __name__ == "__main__":
    unittest.main()
