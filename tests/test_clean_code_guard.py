from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
GUARD = PLUGIN_ROOT / "hooks" / "clean_code_guard.py"
RULES = PLUGIN_ROOT / "hooks" / "clean_code_rules.py"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    # The dataclass decorator resolves its field annotations through
    # sys.modules, so a module executed without being registered there fails on
    # the decorator rather than on anything this suite is testing.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rules = _load(RULES)

# Comment fixtures are written with explicit newlines rather than triple-quoted
# blocks. The scanner reads physical lines and cannot tell a comment inside a
# string literal from a real one, so an indented marker line in this file's own
# source would be reported when the guard scans the test suite.
FLAG_SOURCE = "def render(text, quiet=True):\n    return text\n"
SWALLOW_SOURCE = (
    "def read():\n    try:\n        pass\n    except OSError:\n        pass\n"
)
COMMENTED_SOURCE = "def total(rows):\n    # return sum(rows)\n    return 0\n"
ORPHAN_SOURCE = "def total(rows):\n    # TODO revisit this\n    return 0\n"
TICKETED_SOURCE = "def total(rows):\n    # TODO ABC-1234 revisit this\n    return 0\n"
DEEP_SOURCE = (
    "def walk(items):\n"
    "    for group in items:\n"
    "        if group:\n"
    "            for item in group:\n"
    "                if item:\n"
    "                    return item\n"
    "    return None\n"
)


class CleanCodeRuleTests(unittest.TestCase):
    """The analysis half: one rule, one source, no process and no files."""

    def triggered(self, source: str) -> set[str]:
        findings = rules.analyse(Path("sample.py"), source, rules.load_limits())
        return {finding.rule for finding in findings}

    def test_reports_a_function_longer_than_the_limit(self) -> None:
        body = "\n".join(f"    value_{index} = {index}" for index in range(25))
        self.assertIn("function-length", self.triggered(f"def build():\n{body}\n"))

    def test_reports_more_parameters_than_the_limit(self) -> None:
        source = "def send(host, port, retries, timeout):\n    return host\n"
        self.assertIn("parameter-count", self.triggered(source))

    def test_ignores_self_when_counting_parameters(self) -> None:
        source = "class Client:\n    def send(self, host, port, retries):\n        return host\n"
        self.assertNotIn("parameter-count", self.triggered(source))

    def test_reports_nesting_deeper_than_the_limit(self) -> None:
        self.assertIn("nesting-depth", self.triggered(DEEP_SOURCE))

    def test_reports_a_boolean_flag_parameter(self) -> None:
        self.assertIn("flag-parameter", self.triggered(FLAG_SOURCE))

    def test_reports_an_exception_caught_and_ignored(self) -> None:
        self.assertIn("swallowed-error", self.triggered(SWALLOW_SOURCE))

    def test_reports_commented_out_code(self) -> None:
        self.assertIn("commented-code", self.triggered(COMMENTED_SOURCE))

    def test_reports_a_marker_with_no_ticket(self) -> None:
        self.assertIn("orphan-marker", self.triggered(ORPHAN_SOURCE))

    def test_keeps_a_marker_that_names_a_ticket(self) -> None:
        self.assertNotIn("orphan-marker", self.triggered(TICKETED_SOURCE))

    def test_reports_a_file_that_does_not_parse(self) -> None:
        self.assertIn("parse-error", self.triggered("def broken(\n"))


class CleanCodeHookTests(unittest.TestCase):
    """The hook half, driven the way a host drives it: real process, real stdin."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_guard(self, stdin: str, env=None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(GUARD)],
            input=stdin,
            text=True,
            capture_output=True,
            cwd=str(self.base),
            env=env or os.environ.copy(),
            check=False,
        )

    def write_offender(self, name: str = "offender.py") -> Path:
        path = self.base / name
        path.write_text(SWALLOW_SOURCE, encoding="utf-8")
        return path

    def event(self, path: str, tool: str = "Write") -> str:
        return json.dumps({"tool_name": tool, "tool_input": {"file_path": path}})

    def test_returns_findings_as_post_tool_use_context(self) -> None:
        offender = self.write_offender()
        result = self.run_guard(self.event(str(offender)))
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(payload["hookEventName"], "PostToolUse")
        self.assertIn("swallowed-error", payload["additionalContext"])

    def test_stays_silent_for_a_tool_that_does_not_write(self) -> None:
        offender = self.write_offender()
        result = self.run_guard(self.event(str(offender), tool="Read"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_stays_silent_on_malformed_stdin(self) -> None:
        result = self.run_guard("{not json at all")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_stays_silent_on_empty_stdin(self) -> None:
        result = self.run_guard("")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_stays_silent_for_an_unsupported_extension(self) -> None:
        readme = self.base / "notes.md"
        readme.write_text("# nothing to analyse here\n", encoding="utf-8")
        result = self.run_guard(self.event(str(readme)))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_stays_silent_when_the_written_file_is_clean(self) -> None:
        clean = self.base / "clean.py"
        clean.write_text("def total(rows):\n    return sum(rows)\n", encoding="utf-8")
        result = self.run_guard(self.event(str(clean)))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_strict_mode_exits_two_and_writes_to_stderr(self) -> None:
        offender = self.write_offender()
        env = os.environ.copy()
        env["CLEAN_CODE_GUARD_STRICT"] = "1"
        result = self.run_guard(self.event(str(offender)), env=env)
        self.assertEqual(result.returncode, 2)
        self.assertIn("swallowed-error", result.stderr)
        self.assertEqual(result.stdout.strip(), "")


class CleanCodeScanTests(unittest.TestCase):
    """Scan mode, including the acceptance list that hook mode deliberately skips."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        (self.base / "offender.py").write_text(SWALLOW_SOURCE, encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_scan(self, target: str = ".") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(GUARD), "--scan", target],
            text=True,
            capture_output=True,
            cwd=str(self.base),
            check=False,
        )

    def accept(self, entry: str) -> None:
        listed = self.base / "cleancode-accepted.txt"
        listed.write_text(f"# argued away by a test\n{entry}\n", encoding="utf-8")

    def test_scan_reports_the_finding_and_exits_one(self) -> None:
        result = self.run_scan()
        self.assertEqual(result.returncode, 1)
        self.assertIn("swallowed-error", result.stdout)
        self.assertIn("1 finding(s).", result.stdout)

    def test_an_accepted_finding_is_filtered_out(self) -> None:
        self.accept("offender.py:4:swallowed-error")
        result = self.run_scan()
        self.assertEqual(result.returncode, 0)
        self.assertIn("0 finding(s).", result.stdout)

    def test_acceptance_is_bound_to_its_rule(self) -> None:
        self.accept("offender.py:4:function-length")
        result = self.run_scan()
        self.assertEqual(result.returncode, 1)
        self.assertIn("swallowed-error", result.stdout)

    def test_acceptance_is_bound_to_its_line(self) -> None:
        self.accept("offender.py:99:swallowed-error")
        result = self.run_scan()
        self.assertEqual(result.returncode, 1)
        self.assertIn("swallowed-error", result.stdout)

    def test_hook_mode_does_not_read_the_acceptance_list(self) -> None:
        self.accept("offender.py:4:swallowed-error")
        event = json.dumps(
            {"tool_name": "Write", "tool_input": {"file_path": "offender.py"}}
        )
        result = subprocess.run(
            [sys.executable, str(GUARD)],
            input=event,
            text=True,
            capture_output=True,
            cwd=str(self.base),
            check=False,
        )
        self.assertIn("swallowed-error", result.stdout)


if __name__ == "__main__":
    unittest.main()
