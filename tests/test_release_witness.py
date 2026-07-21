from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PLUGIN_ROOT / "scripts" / "release_witness.py"
SPEC = importlib.util.spec_from_file_location("release_witness", MODULE_PATH)
witness = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(witness)


class ReleaseWitnessTests(unittest.TestCase):
    def test_witness_requires_real_passing_receipts_for_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / "validation.json"
            receipt.write_text(
                json.dumps(
                    {
                        "name": "unit",
                        "passed": True,
                        "command": ["python", "-m", "unittest"],
                    }
                ),
                encoding="utf-8",
            )
            payload = witness.create_witness(PLUGIN_ROOT, [receipt])
        self.assertTrue(payload["releaseReady"])
        self.assertFalse(payload["liveIntegrationsValidated"])
        self.assertEqual(witness.verify_witness(PLUGIN_ROOT, payload), [])

    def test_unvalidated_witness_is_not_release_ready(self) -> None:
        payload = witness.create_witness(PLUGIN_ROOT, [])
        self.assertFalse(payload["releaseReady"])

    def test_changed_file_is_detected(self) -> None:
        payload = witness.create_witness(PLUGIN_ROOT, [])
        payload["files"][0]["sha256"] = "0" * 64
        self.assertTrue(
            any(
                "changed file" in error
                for error in witness.verify_witness(PLUGIN_ROOT, payload)
            )
        )

    def test_malformed_receipt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / "bad.json"
            receipt.write_text('{"name":"unit","passed":true}', encoding="utf-8")
            with self.assertRaises(witness.WitnessError):
                witness.create_witness(PLUGIN_ROOT, [receipt])


if __name__ == "__main__":
    unittest.main()
