from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PLUGIN_ROOT / "benchmarks" / "evaluators" / "bug_fix_quality.py"
SPEC = importlib.util.spec_from_file_location("bug_fix_quality", MODULE_PATH)
quality = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(quality)


class BugFixQualityTests(unittest.TestCase):
    def test_scores_behavior_test_discipline_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src" / "payments").mkdir(parents=True)
            (root / "tests").mkdir()
            (root / "src" / "payments" / "retry_policy.py").write_text(
                "class RetryPolicy:\n"
                "    def __init__(self, maximum_attempts=4): self.maximum_attempts=maximum_attempts\n"
                "    def should_retry(self, status, attempt): return status == 425 and attempt < self.maximum_attempts\n",
                encoding="utf-8",
            )
            (root / "tests" / "test_retry_policy.py").write_text(
                "# regression: should_retry(425, 1)\n", encoding="utf-8"
            )
            events = root / "events.jsonl"
            events.write_text(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "command_execution",
                            "exit_code": 0,
                            "command": "python -m unittest tests.test_retry_policy",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            message = root / "message.txt"
            message.write_text("HTTP 425 fixed; focused test passed.", encoding="utf-8")

            report = quality.evaluate(root, events, message)

            self.assertEqual(report["score"], 100)
            self.assertEqual(report["critical_errors"], [])


if __name__ == "__main__":
    unittest.main()
