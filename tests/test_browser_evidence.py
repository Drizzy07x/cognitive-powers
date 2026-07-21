from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT / "skills" / "verify-web-behavior" / "scripts" / "browser_evidence.py"
)


def load_browser_evidence():
    spec = importlib.util.spec_from_file_location(
        "test_browser_evidence_module", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


browser_evidence = load_browser_evidence()


class PlaywrightRunner:
    def __init__(self, report: dict[str, object], exit_code: int = 0) -> None:
        self.report = report
        self.exit_code = exit_code
        self.calls: list[list[str]] = []

    def __call__(
        self, command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "Version 1.60.0\n", "")
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        report_path = Path(str(environment["PLAYWRIGHT_JSON_OUTPUT_FILE"]))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(self.report), encoding="utf-8")
        trace = report_path.parent / "test-results" / "flow" / "trace.zip"
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.write_bytes(b"real trace bytes")
        return subprocess.CompletedProcess(command, self.exit_code, "runner output", "")


def report(expected: int = 1, unexpected: int = 0) -> dict[str, object]:
    return {
        "suites": [
            {
                "specs": [
                    {
                        "title": "submits payment",
                        "file": "tests/payment.spec.ts",
                        "line": 4,
                        "tests": [
                            {
                                "projectName": "chromium",
                                "expectedStatus": "passed",
                                "status": "expected"
                                if unexpected == 0
                                else "unexpected",
                                "results": [
                                    {
                                        "status": "passed"
                                        if unexpected == 0
                                        else "failed"
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        ],
        "errors": [],
        "stats": {
            "expected": expected,
            "unexpected": unexpected,
            "flaky": 0,
            "skipped": 0,
            "duration": 125,
        },
    }


class BrowserEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.root = self.base / "workspace"
        self.root.mkdir()
        (self.root / "playwright.config.ts").write_text(
            "export default {};\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_probe_requires_existing_config_and_executable(self) -> None:
        (self.root / "playwright.config.ts").unlink()
        result = browser_evidence.probe(
            self.root,
            executable="missing-cognitive-powers-playwright",
        )

        self.assertFalse(result["usable"])
        self.assertIn("config not found", result["reason"])
        self.assertIn("executable not found", result["reason"])

    def test_successful_run_normalizes_and_hashes_real_artifacts(self) -> None:
        runner = PlaywrightRunner(report())
        artifact_dir = self.base / "browser-run"

        receipt, exit_code = browser_evidence.run_tests(
            self.root,
            executable=sys.executable,
            selectors=["tests/payment.spec.ts"],
            projects=["chromium"],
            artifact_dir=artifact_dir,
            runner=runner,
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(receipt["passed"])
        self.assertEqual(receipt["stats"]["expected"], 1)
        self.assertEqual(receipt["tests"][0]["project"], "chromium")
        paths = {item["path"] for item in receipt["artifacts"]}
        self.assertIn("playwright-report.json", paths)
        self.assertIn("test-results/flow/trace.zip", paths)
        self.assertTrue(Path(receipt["receipt"]).is_file())
        self.assertIn("--trace=retain-on-failure", runner.calls[1])

    def test_unexpected_result_cannot_be_reported_as_passed(self) -> None:
        runner = PlaywrightRunner(report(expected=0, unexpected=1), exit_code=1)

        receipt, exit_code = browser_evidence.run_tests(
            self.root,
            executable=sys.executable,
            artifact_dir=self.base / "failed-run",
            runner=runner,
        )

        self.assertEqual(exit_code, 1)
        self.assertFalse(receipt["passed"])
        self.assertEqual(receipt["stats"]["unexpected"], 1)

    def test_artifacts_inside_workspace_are_rejected(self) -> None:
        runner = PlaywrightRunner(report())
        with self.assertRaises(browser_evidence.BrowserEvidenceError):
            browser_evidence.run_tests(
                self.root,
                executable=sys.executable,
                artifact_dir=self.root / "test-results",
                runner=runner,
            )


if __name__ == "__main__":
    unittest.main()
