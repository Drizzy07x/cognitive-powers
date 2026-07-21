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
    PLUGIN_ROOT / "skills" / "execute-durably" / "scripts" / "plan_compiler.py"
)
WORK_STATE_PATH = (
    PLUGIN_ROOT / "skills" / "execute-durably" / "scripts" / "work_state.py"
)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


plan_compiler = load_module("test_plan_compiler_module", SCRIPT_PATH)
work_state = load_module("test_plan_compiler_work_state_module", WORK_STATE_PATH)


def packet(
    packet_id: str = "parser",
    *,
    owned_path: str = "src/parser",
    dependency: str = "none",
) -> str:
    return f"""## Packet: `{packet_id}`
### Objective
Implement {packet_id} without changing its public API.

### Owned paths
- `{owned_path}`

### Depends on
- `{dependency}`

### Invariants
- Existing CLI inputs remain compatible

### Checks
- `[\"py\", \"-3\", \"-m\", \"unittest\", \"tests.test_{packet_id}\"]`

### Integration notes
- Run the integrated suite after dependent packets
"""


class PlanCompilerTests(unittest.TestCase):
    def test_compiles_dependency_plan_accepted_by_work_state(self) -> None:
        markdown = (
            "# Work packet plan\n\n"
            + packet("parser")
            + "\n"
            + packet("cli", owned_path="src/cli.py", dependency="parser")
        )

        result = plan_compiler.compile_markdown(markdown)

        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["packets"][0]["dependencies"], [])
        self.assertEqual(result["packets"][1]["dependencies"], ["parser"])
        self.assertEqual(
            result["packets"][0]["checks"][0],
            ["py", "-3", "-m", "unittest", "tests.test_parser"],
        )
        validated = work_state._validate_packet_plan(result)
        self.assertEqual([item["id"] for item in validated], ["parser", "cli"])

    def test_acceptance_criteria_alias_emits_required_invariants(self) -> None:
        markdown = packet().replace("### Invariants", "### Acceptance criteria")

        result = plan_compiler.compile_markdown(markdown)

        self.assertEqual(
            result["packets"][0]["invariants"],
            ["Existing CLI inputs remain compatible"],
        )

    def test_rejects_unknown_section_instead_of_silently_dropping_it(self) -> None:
        markdown = packet().replace("### Integration notes", "### Rollout notes")

        with self.assertRaisesRegex(
            plan_compiler.PlanCompilerError, "unknown packet section"
        ):
            plan_compiler.compile_markdown(markdown)

    def test_rejects_shell_string_check(self) -> None:
        markdown = packet().replace(
            '`["py", "-3", "-m", "unittest", "tests.test_parser"]`',
            "py -3 -m unittest tests.test_parser",
        )

        with self.assertRaisesRegex(plan_compiler.PlanCompilerError, "JSON argv array"):
            plan_compiler.compile_markdown(markdown)

    def test_rejects_empty_argument_in_check(self) -> None:
        markdown = packet().replace(
            '["py", "-3", "-m", "unittest", "tests.test_parser"]',
            '["py", ""]',
        )

        with self.assertRaisesRegex(
            plan_compiler.PlanCompilerError, "non-empty strings"
        ):
            plan_compiler.compile_markdown(markdown)

    def test_rejects_unknown_dependency(self) -> None:
        with self.assertRaisesRegex(
            plan_compiler.PlanCompilerError, "unknown dependency missing"
        ):
            plan_compiler.compile_markdown(packet(dependency="missing"))

    def test_rejects_duplicate_and_unstable_packet_ids(self) -> None:
        with self.assertRaisesRegex(
            plan_compiler.PlanCompilerError, "duplicate packet id"
        ):
            plan_compiler.compile_markdown(
                packet("same", owned_path="src/one.py")
                + packet("same", owned_path="src/two.py")
            )
        with self.assertRaisesRegex(
            plan_compiler.PlanCompilerError, "stable identifier"
        ):
            plan_compiler.compile_markdown(packet("not stable"))

    def test_rejects_dependency_cycle(self) -> None:
        markdown = packet("one", owned_path="src/one.py", dependency="two") + packet(
            "two", owned_path="src/two.py", dependency="one"
        )

        with self.assertRaisesRegex(
            plan_compiler.PlanCompilerError, "dependency cycle"
        ):
            plan_compiler.compile_markdown(markdown)

    def test_rejects_cross_packet_owned_path_overlap(self) -> None:
        markdown = packet("one", owned_path="src") + packet(
            "two", owned_path="src/two.py"
        )

        with self.assertRaisesRegex(
            plan_compiler.PlanCompilerError, "overlap ownership"
        ):
            plan_compiler.compile_markdown(markdown)

    def test_rejects_traversing_owned_path(self) -> None:
        with self.assertRaisesRegex(
            plan_compiler.PlanCompilerError, "must not traverse"
        ):
            plan_compiler.compile_markdown(packet(owned_path="src/../outside.py"))

    def test_cli_writes_atomic_shape_to_requested_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "plan.md"
            output = root / "plan.json"
            source.write_text(packet(), encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "compile",
                    "--input",
                    str(source),
                    "--output",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(
                completed.returncode, 0, completed.stdout + completed.stderr
            )
            self.assertEqual(completed.stdout, "")
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8"))["schema_version"], 1
            )

    def test_cli_failure_does_not_create_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "plan.md"
            output = root / "plan.json"
            source.write_text("# Empty plan\n", encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "--input",
                    str(source),
                    "--output",
                    str(output),
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("at least one packet", json.loads(completed.stdout)["error"])
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
