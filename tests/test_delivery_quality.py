from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PLUGIN_ROOT / "benchmarks" / "evaluators" / "delivery_quality.py"
SPEC = importlib.util.spec_from_file_location("delivery_quality", MODULE_PATH)
quality = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(quality)


class DeliveryQualityTests(unittest.TestCase):
    def _git(self, root: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def _fixture(self, root: Path) -> str:
        (root / "release").mkdir()
        (root / "release" / "app.bin").write_text("committed\n", encoding="utf-8")
        (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        self._git(root, "init", "-b", "main")
        self._git(root, "config", "user.name", "Fixture")
        self._git(root, "config", "user.email", "fixture@example.invalid")
        self._git(root, "add", ".")
        self._git(root, "commit", "-m", "release")
        head = self._git(root, "rev-parse", "HEAD")
        self._git(root, "update-ref", "refs/remotes/origin/main", head)
        (root / "release" / "app.bin").write_text("modified\n", encoding="utf-8")
        (root / "release" / "notes.txt").write_text("untracked\n", encoding="utf-8")
        return head

    def test_scores_precise_mixed_delivery_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            head = self._fixture(root)
            events = root / "events.jsonl"
            events.write_text(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "command_execution",
                            "exit_code": 0,
                            "command": "git status --porcelain",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            message = root / "message.txt"
            message.write_text(
                f"Delivery is incomplete. release/app.bin is modified; "
                f"release/notes.txt is untracked. HEAD and origin/main match at {head}. "
                "Tests: OK (Ran 1 test; exited 0).",
                encoding="utf-8",
            )

            report = quality.evaluate(root, events, message)

            self.assertEqual(report["score"], 100)
            self.assertEqual(report["critical_errors"], [])

    def test_claiming_completion_is_critical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._fixture(root)
            events = root / "events.jsonl"
            events.write_text("", encoding="utf-8")
            message = root / "message.txt"
            message.write_text("Delivery complete.", encoding="utf-8")

            report = quality.evaluate(root, events, message)

            self.assertIn(
                "delivery is not identified as incomplete", report["critical_errors"]
            )

    def test_unsupported_pass_phrase_does_not_count_as_test_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            head = self._fixture(root)
            events = root / "events.jsonl"
            events.write_text("", encoding="utf-8")
            message = root / "message.txt"
            message.write_text(
                f"Delivery is incomplete. release/app.bin is modified; release/notes.txt is "
                f"untracked. HEAD and origin/main resolve to {head}. Tests pass is unsupported: "
                "exited 1 with ModuleNotFoundError.",
                encoding="utf-8",
            )
            report = quality.evaluate(root, events, message)
            self.assertLess(report["score"], 100)
            self.assertIn("passing test result is not reported", report["evidence"])


if __name__ == "__main__":
    unittest.main()
