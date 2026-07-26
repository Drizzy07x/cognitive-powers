"""The self-check exists to observe behaviour, so it must not fake a pass."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "selfcheck.py"


def load_module():
    spec = importlib.util.spec_from_file_location("test_selfcheck_module", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


selfcheck = load_module()


class SelfCheckTests(unittest.TestCase):
    def test_this_checkout_passes_every_check(self) -> None:
        report = selfcheck.run_checks()
        failed = [check for check in report["checks"] if check["status"] == "fail"]
        self.assertEqual(failed, [], failed)
        self.assertTrue(report["passed"])
        self.assertTrue(report["observed"])

    def test_it_reports_the_checks_that_prove_the_install_runs(self) -> None:
        names = {check["name"] for check in selfcheck.run_checks()["checks"]}
        for required in (
            "interpreter",
            "hooks.post_tool_use",
            "hooks.stop",
            "hooks.session_start",
            "evidence.shared_root",
            "evidence.round_trip",
        ):
            with self.subTest(check=required):
                self.assertIn(required, names)

    def test_a_missing_optional_provider_is_never_a_failure(self) -> None:
        """Absence is a supported configuration; reporting it as broken misleads."""
        original = selfcheck.shutil.which
        selfcheck.shutil.which = lambda name: None
        try:
            results = selfcheck.check_optional_providers()
        finally:
            selfcheck.shutil.which = original
        self.assertTrue(results)
        for result in results:
            with self.subTest(check=result["name"]):
                self.assertEqual(result["status"], "skipped")

    def test_it_names_what_only_the_model_can_observe(self) -> None:
        """A script beside the host cannot see the host's own skill listing."""
        required = selfcheck.run_checks()["hostObservationsRequired"]
        self.assertEqual(len(required), 2)
        self.assertTrue(all(isinstance(item, str) and item for item in required))

    def test_the_checks_leave_nothing_behind_in_the_plugin(self) -> None:
        before = {path for path in PLUGIN_ROOT.rglob("*") if path.is_file()}
        selfcheck.run_checks()
        after = {path for path in PLUGIN_ROOT.rglob("*") if path.is_file()}
        self.assertEqual(after - before, set())

    def test_the_cli_emits_a_machine_readable_report(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        report = json.loads(completed.stdout)
        self.assertEqual(report["kind"], "cognitive_powers_selfcheck")
        self.assertEqual(completed.returncode, 0 if report["passed"] else 1)

    def test_a_broken_check_is_reported_rather_than_raised(self) -> None:
        original = selfcheck.check_interpreter
        selfcheck.check_interpreter = lambda: (_ for _ in ()).throw(
            RuntimeError("probe exploded")
        )
        try:
            code = selfcheck.main(["--json"])
        finally:
            selfcheck.check_interpreter = original
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
