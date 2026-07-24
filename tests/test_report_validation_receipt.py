from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PLUGIN_ROOT / "scripts" / "report_validation_receipt.py"
SPEC = importlib.util.spec_from_file_location("report_validation_receipt", MODULE_PATH)
reporter = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(reporter)


def validation_receipt(*, passed: bool) -> dict[str, object]:
    failed = {
        "name": "tests",
        "category": "offline",
        "command": ["python", "-m", "unittest", "discover", "-s", "tests", "-v"],
        "exitCode": 0 if passed else 1,
        "passed": passed,
        "durationSeconds": 1.25,
        "stdoutSha256": "1" * 64,
        "stderrSha256": "2" * 64,
        "stdoutTail": "" if passed else "stdout failure",
        "stderrTail": "" if passed else "stderr failure",
    }
    return {
        "schemaVersion": 1,
        "kind": "cognitive-powers-validation",
        "passed": passed,
        "offline": {"complete": True, "passed": passed},
        "git": {
            "initialSha": "a" * 40,
            "sha": "a" * 40,
            "dirty": False,
            "status": [],
            "identityStable": True,
        },
        "source": {
            "initialSha256": "b" * 64,
            "sha256": "b" * 64,
            "identityStable": True,
        },
        "commands": [failed],
    }


class ReportValidationReceiptTests(unittest.TestCase):
    def _write_receipt(self, root: Path, *, passed: bool) -> tuple[Path, bytes]:
        path = root / "validation.json"
        raw = (
            json.dumps(validation_receipt(passed=passed), indent=2).encode("utf-8")
            + b"\n"
        )
        path.write_bytes(raw)
        return path, raw

    def test_summary_exposes_bound_identity_failure_and_receipt_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            receipt, raw = self._write_receipt(Path(temporary), passed=False)
            summary = reporter.build_validation_summary(receipt)

        self.assertFalse(summary["validation_passed"])
        self.assertFalse(summary["passed"])
        self.assertEqual(summary["offline"], {"complete": True, "passed": False})
        self.assertEqual(summary["git"]["initialSha"], "a" * 40)
        self.assertEqual(summary["git"]["finalSha"], "a" * 40)
        self.assertFalse(summary["git"]["dirty"])
        self.assertEqual(summary["git"]["status"], [])
        self.assertEqual(summary["source"]["initialSha256"], "b" * 64)
        self.assertEqual(summary["source"]["finalSha256"], "b" * 64)
        self.assertTrue(summary["identityStable"])
        self.assertEqual(
            summary["receipt_sha256"],
            hashlib.sha256(raw).hexdigest(),
        )
        self.assertEqual(
            summary["failedCommands"],
            [
                {
                    "name": "tests",
                    "argv": [
                        "python",
                        "-m",
                        "unittest",
                        "discover",
                        "-s",
                        "tests",
                        "-v",
                    ],
                    "exitCode": 1,
                    "stdoutTail": "stdout failure",
                    "stderrTail": "stderr failure",
                }
            ],
        )

    def test_valid_failed_receipt_is_reported_without_failing_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt, raw = self._write_receipt(root, passed=False)
            github_output = root / "github-output.txt"
            stdout = io.StringIO()
            with (
                mock.patch.dict(
                    os.environ,
                    {"GITHUB_OUTPUT": str(github_output)},
                    clear=False,
                ),
                redirect_stdout(stdout),
            ):
                exit_code = reporter.main(["--receipt", str(receipt)])

            output = json.loads(stdout.getvalue())
            outputs = github_output.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertFalse(output["validation_passed"])
        self.assertIn("validation_passed=false", outputs)
        self.assertIn(
            f"receipt_sha256={hashlib.sha256(raw).hexdigest()}",
            outputs,
        )

    def test_missing_or_invalid_receipt_fails_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = root / "missing.json"
            invalid = root / "invalid.json"
            invalid.write_text("{not-json", encoding="utf-8")

            for receipt in (missing, invalid):
                with self.subTest(receipt=receipt.name):
                    stdout = io.StringIO()
                    with redirect_stdout(stdout):
                        exit_code = reporter.main(["--receipt", str(receipt)])
                    self.assertEqual(exit_code, 2)
                    self.assertIn("error", json.loads(stdout.getvalue()))

    def test_publication_failure_is_explicit_and_blocks_release_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            receipt, _ = self._write_receipt(Path(temporary), passed=True)
            validation = reporter.build_validation_summary(receipt)

        publication = reporter.build_publication_summary(validation, "failure")
        self.assertTrue(publication["validation_passed"])
        self.assertFalse(publication["receipt_uploaded"])
        self.assertEqual(publication["artifact_publication_outcome"], "failure")
        self.assertTrue(publication["release_preparation_blocked"])
        self.assertFalse(publication["release_ready_claimed"])

    def test_successful_upload_does_not_claim_release_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            receipt, _ = self._write_receipt(Path(temporary), passed=True)
            validation = reporter.build_validation_summary(receipt)

        publication = reporter.build_publication_summary(validation, "success")
        self.assertTrue(publication["receipt_uploaded"])
        self.assertFalse(publication["release_preparation_blocked"])
        self.assertFalse(publication["release_ready_claimed"])


if __name__ == "__main__":
    unittest.main()
