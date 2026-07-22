from __future__ import annotations
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "real_host_quality", ROOT / "benchmarks/evaluators/real_host_quality.py"
)
quality = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(quality)


class RealHostQualityTests(unittest.TestCase):
    def test_scores_complete_hashed_public_surface_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            screenshot = root / "final.png"
            screenshot.write_bytes(b"real screenshot")
            state = {
                "ticket": "QCU-217",
                "selected": True,
                "assignee": "Maya Chen",
                "priority": "High",
                "note": "Validated in staging",
                "status": "Ready",
                "events": quality.EXPECTED_EVENTS,
            }
            (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
            action = {
                "host": "chromium-public-surface",
                "visible": {"status": "Ready"},
                "screenshotSha256": hashlib.sha256(screenshot.read_bytes()).hexdigest(),
            }
            action_path = root / "host-receipt.json"
            action_path.write_text(json.dumps(action), encoding="utf-8")
            action_hash = hashlib.sha256(action_path.read_bytes()).hexdigest()
            observer = {
                "host": "fresh-chromium-public-surface",
                "visible": {"status": "Ready"},
                "events": quality.EXPECTED_EVENTS,
                "actionReceiptSha256": action_hash,
            }
            observer_path = root / "observer-receipt.json"
            observer_path.write_text(json.dumps(observer), encoding="utf-8")
            observer_hash = hashlib.sha256(observer_path.read_bytes()).hexdigest()
            message = root / "message.txt"
            message.write_text(f"{action_hash} {observer_hash}", encoding="utf-8")
            report = quality.evaluate(root, root / "events.jsonl", message)
            self.assertEqual(report["score"], 100)
            self.assertEqual(report["critical_errors"], [])

    def test_rejects_incomplete_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "state.json").write_text(
                '{"status":"Draft","events":[]}', encoding="utf-8"
            )
            message = root / "message.txt"
            message.write_text("done", encoding="utf-8")
            report = quality.evaluate(root, root / "events.jsonl", message)
            self.assertIn(
                "the public host did not reach the exact target state",
                report["critical_errors"],
            )


if __name__ == "__main__":
    unittest.main()
