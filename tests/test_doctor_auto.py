from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import doctor

BASE = "MINIMUM_STORIES = 12\n\ndef gate(x):\n    return x\n"


class AutoMergeGuardTests(unittest.TestCase):
    def _repo(self, gates_after: str, extra: dict[str, str] | None = None) -> Path:
        root = Path(tempfile.mkdtemp())

        def git(*args: str) -> None:
            doctor.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], root)

        git("init", "-b", "main")
        (root / "tests").mkdir()
        (root / "validation_gates.py").write_text(BASE, encoding="utf-8", newline="")
        git("add", "-A")
        git("commit", "-m", "base")
        git("checkout", "-b", "doctor/2026-10-28")
        (root / "validation_gates.py").write_text(gates_after, encoding="utf-8", newline="")
        (root / "tests" / "test_incident_2026_10_28.py").write_text("import unittest\n", encoding="utf-8", newline="")
        for name, body in (extra or {}).items():
            (root / name).write_text(body, encoding="utf-8", newline="")
        git("add", "-A")
        git("commit", "-m", "fix")
        return root

    def test_small_additive_fix_is_approved(self) -> None:
        root = self._repo(BASE + "\ndef repair(x):\n    return x\n")
        self.assertEqual(doctor.auto_merge_ok(root, "doctor/2026-10-28"), (True, ""))

    def test_lowering_a_threshold_is_not_approved(self) -> None:
        root = self._repo(BASE.replace("12", "5"))
        ok, why = doctor.auto_merge_ok(root, "doctor/2026-10-28")
        self.assertFalse(ok)
        self.assertIn("threshold", why)

    def test_touching_blocked_files_is_not_approved(self) -> None:
        root = self._repo(BASE + "\ndef repair(x):\n    return x\n", {"alerts.py": "x = 1\n"})
        self.assertFalse(doctor.auto_merge_ok(root, "doctor/2026-10-28")[0])

    def test_suppressing_errors_is_not_approved(self) -> None:
        root = self._repo(BASE + "\ndef repair(x):\n    try:\n        return x\n    except Exception: pass\n")
        self.assertFalse(doctor.auto_merge_ok(root, "doctor/2026-10-28")[0])

    def test_incident_count_reads_the_ledger(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.assertEqual(doctor.incident_count(root, "k"), 0)
        doctor.record_incident(root, "2026-10-27", "k", None)
        self.assertEqual(doctor.incident_count(root, "k"), 1)


if __name__ == "__main__":
    unittest.main()
