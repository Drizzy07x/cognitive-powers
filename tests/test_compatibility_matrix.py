from __future__ import annotations

import importlib.util
import hashlib
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "build_compatibility_matrix.py"
IDENTITY_PATH = ROOT / "scripts" / "release_identity.py"


def load(path: Path = MODULE_PATH, name: str = "compatibility"):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def release_tag() -> str:
    """The tag under test is whatever this checkout declares, never a literal."""
    return load(IDENTITY_PATH, "release_identity_matrix_test").release_tag()


class CompatibilityMatrixTests(unittest.TestCase):
    def receipt(self, **overrides):
        value = {
            "schemaVersion": 2,
            "os": "windows-latest",
            "python": "3.11",
            "codexCli": "1.2.3",
            "scenario": "clean-install",
            "passed": True,
            "identity": {
                "commit": "a" * 40,
                "workflow": "validate.yml",
                "runId": "123",
                "runAttempt": 1,
            },
            "installation": {
                "commit": "a" * 40,
                "tag": release_tag(),
                "reportSha256": "c" * 64,
            },
            "attestation": {
                "kind": "github-actions-validation",
                "validationReceiptSha256": "b" * 64,
                "verified": False,
            },
        }
        value.update(overrides)
        canonical = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        value["receiptSha256"] = hashlib.sha256(canonical).hexdigest()
        return value

    def test_committed_unknown_matrix_is_a_reproducible_contract_gate(self) -> None:
        module = load()
        contract = json.loads(
            (ROOT / "compatibility-contract.json").read_text(encoding="utf-8")
        )
        matrix = json.loads(
            (ROOT / "compatibility-matrix.json").read_text(encoding="utf-8")
        )
        documentation = (ROOT / "docs" / "compatibility.md").read_text(encoding="utf-8")
        self.assertTrue(module.outputs_match(contract, [], matrix, documentation))
        matrix["rows"][0]["status"] = "compatible"
        self.assertFalse(module.outputs_match(contract, [], matrix, documentation))

    def test_generated_artifacts_are_written_in_lf_bytes(self) -> None:
        # Both outputs are tracked files. Text mode wrote CRLF on Windows,
        # invisible to git status (gitattributes normalizes on commit) and to
        # --check (universal-newline read); only the release witness's byte
        # digest ever saw it, far from the cause.
        module = load(name="compatibility_bytes")
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            code = module.main(
                [
                    "--contract",
                    str(ROOT / "compatibility-contract.json"),
                    "--json-output",
                    str(parent / "matrix.json"),
                    "--markdown-output",
                    str(parent / "matrix.md"),
                ]
            )
            self.assertEqual(code, 0)
            json_raw = (parent / "matrix.json").read_bytes()
            markdown_raw = (parent / "matrix.md").read_bytes()
        self.assertNotIn(b"\r\n", json_raw)
        self.assertNotIn(b"\r\n", markdown_raw)
        self.assertTrue(json_raw.endswith(b"\n"))

    def test_matrix_is_generated_from_receipts_and_missing_combinations_are_unknown(
        self,
    ) -> None:
        module = load()
        contract = {
            "schemaVersion": 1,
            "axes": {
                "os": ["windows-latest", "ubuntu-latest"],
                "python": ["3.11"],
                "codexCli": ["1.2.3"],
            },
            "scenarios": [
                "clean-install",
                "upgrade-v1.5.2",
                "rollback",
                "corrupt-state",
                "legacy-copy",
                "checkout-without-git",
                "crlf-lf",
                "symlink",
                "unicode-space-path",
            ],
        }
        receipts = [self.receipt()]
        matrix = module.build_matrix(
            contract,
            receipts,
            verified_receipt_digests={receipts[0]["receiptSha256"]},
        )
        self.assertEqual(
            matrix["summary"], {"compatible": 1, "incompatible": 0, "unknown": 17}
        )
        known = next(
            row
            for row in matrix["rows"]
            if row["os"] == "windows-latest" and row["scenario"] == "clean-install"
        )
        unknown = next(
            row
            for row in matrix["rows"]
            if row["os"] == "ubuntu-latest" and row["scenario"] == "clean-install"
        )
        self.assertEqual(known["status"], "compatible")
        self.assertEqual(unknown["status"], "unknown")
        self.assertEqual(unknown["evidence"], [])

    def test_conflicting_or_malformed_receipts_fail_closed(self) -> None:
        module = load()
        contract = {
            "schemaVersion": 1,
            "axes": {
                "os": ["windows-latest"],
                "python": ["3.11"],
                "codexCli": ["1.2.3"],
            },
            "scenarios": ["clean-install"],
        }
        passing = self.receipt()
        failing = self.receipt(passed=False)
        with self.assertRaisesRegex(module.CompatibilityError, "conflicting"):
            module.build_matrix(
                contract,
                [passing, failing],
                verified_receipt_digests={
                    passing["receiptSha256"],
                    failing["receiptSha256"],
                },
            )

    def test_tampered_or_unattested_receipts_never_become_compatible(self) -> None:
        module = load()
        contract = {
            "schemaVersion": 1,
            "axes": {
                "os": ["windows-latest"],
                "python": ["3.11"],
                "codexCli": ["1.2.3"],
            },
            "scenarios": ["clean-install"],
        }
        tampered = self.receipt()
        tampered["passed"] = False
        missing = self.receipt()
        missing.pop("attestation")
        self_asserted = self.receipt()
        for receipt in (tampered, missing, self_asserted):
            matrix = module.build_matrix(contract, [receipt])
            self.assertEqual(matrix["rows"][0]["status"], "unknown")
            self.assertEqual(matrix["rows"][0]["evidence"], [])

    def test_self_asserted_attestation_is_rejected_even_when_digest_is_supplied(
        self,
    ) -> None:
        module = load()
        contract = {
            "schemaVersion": 1,
            "axes": {
                "os": ["windows-latest"],
                "python": ["3.11"],
                "codexCli": ["1.2.3"],
            },
            "scenarios": ["clean-install"],
        }
        receipt = self.receipt()
        receipt["attestation"]["verified"] = True
        unsigned = {
            key: value for key, value in receipt.items() if key != "receiptSha256"
        }
        receipt["receiptSha256"] = hashlib.sha256(
            json.dumps(
                unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()

        matrix = module.build_matrix(
            contract,
            [receipt],
            verified_receipt_digests={receipt["receiptSha256"]},
        )

        self.assertEqual(matrix["rows"][0]["status"], "unknown")

    def test_trusted_digest_without_bound_installation_report_is_unknown(self) -> None:
        module = load()
        contract = {
            "schemaVersion": 1,
            "axes": {
                "os": ["windows-latest"],
                "python": ["3.11"],
                "codexCli": ["1.2.3"],
            },
            "scenarios": ["clean-install"],
        }
        receipt = self.receipt()
        receipt.pop("installation")
        unsigned = {
            key: value for key, value in receipt.items() if key != "receiptSha256"
        }
        receipt["receiptSha256"] = hashlib.sha256(
            json.dumps(
                unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()

        matrix = module.build_matrix(
            contract,
            [receipt],
            verified_receipt_digests={receipt["receiptSha256"]},
        )
        self.assertEqual(matrix["rows"][0]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
