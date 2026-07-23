from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PLUGIN_ROOT / "scripts" / "validate_all.py"
SPEC = importlib.util.spec_from_file_location("validate_all", MODULE_PATH)
validator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(validator)
WITNESS_PATH = PLUGIN_ROOT / "scripts" / "release_witness.py"
WITNESS_SPEC = importlib.util.spec_from_file_location(
    "release_witness_contract", WITNESS_PATH
)
release_witness = importlib.util.module_from_spec(WITNESS_SPEC)
assert WITNESS_SPEC.loader is not None
WITNESS_SPEC.loader.exec_module(release_witness)


def command_result(command, category: str, *, passed: bool = True):
    return {
        "name": command.name,
        "category": category,
        "command": ["python", *command.argv],
        "exitCode": 0 if passed else 7,
        "passed": passed,
        "durationSeconds": 0.1,
        "stdoutSha256": "0" * 64,
        "stderrSha256": "0" * 64,
        "stdoutTail": "",
        "stderrTail": "",
    }


class ValidateAllTests(unittest.TestCase):
    def test_ci_installs_pinned_validation_dependencies(self) -> None:
        requirements_path = PLUGIN_ROOT / "requirements-dev.txt"
        requirements = requirements_path.read_text(encoding="utf-8").splitlines()
        declared = [
            line.strip()
            for line in requirements
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(declared, ["ruff==0.15.21"])

        workflow = (PLUGIN_ROOT / ".github" / "workflows" / "validate.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("python -m pip install -r requirements-dev.txt", workflow)
        self.assertLess(
            workflow.index("python -m pip install -r requirements-dev.txt"),
            workflow.index("python scripts/validate_all.py --offline"),
        )

        readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            "& $python -m pip install -r requirements-dev.txt",
            readme,
        )

    def test_release_witness_uses_same_offline_command_contract(self) -> None:
        signature = [
            (command.name, command.argv) for command in validator.OFFLINE_COMMANDS
        ]
        self.assertEqual(signature, list(release_witness.EXPECTED_OFFLINE_COMMANDS))

    def test_readme_offline_list_matches_orchestrator(self) -> None:
        lines = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        declared: list[tuple[str, ...]] = []
        saw_canonical_surface = False
        in_validate_block = False
        for line in lines:
            if line.startswith(
                "The canonical offline surface executed by that entrypoint"
            ):
                saw_canonical_surface = True
                continue
            if saw_canonical_surface and line == "```powershell":
                in_validate_block = True
                continue
            if in_validate_block and line == "```":
                break
            prefix = "& $python "
            if in_validate_block and line.startswith(prefix):
                argv = tuple(line[len(prefix) :].split())
                if argv != ("--version",):
                    declared.append(argv)
        expected = [command.argv for command in validator.OFFLINE_COMMANDS]
        self.assertEqual(declared, expected)

    def test_offline_receipt_is_bound_and_complete(self) -> None:
        with (
            mock.patch.object(
                validator,
                "git_identity",
                return_value={"sha": "a" * 40, "dirty": False, "status": []},
            ),
            mock.patch.object(
                validator,
                "source_identity",
                return_value={"sha256": "b" * 64, "fileCount": 10},
            ),
            mock.patch.object(
                validator,
                "run_command",
                side_effect=lambda root, python, command, category: command_result(
                    command, category
                ),
            ),
        ):
            receipt = validator.build_receipt(
                PLUGIN_ROOT,
                python="python",
                run_offline=True,
                run_live=False,
            )
        self.assertTrue(receipt["passed"])
        self.assertTrue(receipt["offline"]["complete"])
        self.assertTrue(receipt["offline"]["passed"])
        self.assertFalse(receipt["live"]["validated"])
        self.assertTrue(receipt["offlinePassed"])
        self.assertFalse(receipt["liveValidated"])
        self.assertEqual(receipt["git"]["sha"], "a" * 40)
        self.assertEqual(receipt["source"]["sha256"], "b" * 64)
        self.assertEqual(len(receipt["commands"]), len(validator.OFFLINE_COMMANDS))

    def test_one_failed_exit_fails_closed(self) -> None:
        def execute(root, python, command, category):
            return command_result(
                command,
                category,
                passed=command is not validator.OFFLINE_COMMANDS[2],
            )

        with (
            mock.patch.object(
                validator,
                "git_identity",
                return_value={"sha": "a" * 40, "dirty": False, "status": []},
            ),
            mock.patch.object(
                validator,
                "source_identity",
                return_value={"sha256": "b" * 64, "fileCount": 10},
            ),
            mock.patch.object(validator, "run_command", side_effect=execute),
        ):
            receipt = validator.build_receipt(
                PLUGIN_ROOT,
                python="python",
                run_offline=True,
                run_live=False,
            )
        self.assertFalse(receipt["passed"])
        self.assertFalse(receipt["offline"]["passed"])
        self.assertEqual(receipt["commands"][2]["exitCode"], 7)

    def test_live_requires_explicit_immutable_argv(self) -> None:
        with self.assertRaises(validator.ValidationError):
            validator.build_receipt(
                PLUGIN_ROOT,
                python="python",
                run_offline=False,
                run_live=True,
            )
        parsed = validator._live_commands(['["provider-check", "--real"]'])
        self.assertEqual(parsed[0].argv, ("provider-check", "--real"))
        with self.assertRaises(validator.ValidationError):
            validator._live_commands(["provider-check --real"])

    def test_real_nonzero_exit_is_recorded(self) -> None:
        command = validator.ValidationCommand(
            "intentional-failure", ("-c", "raise SystemExit(7)")
        )
        record = validator.run_command(
            PLUGIN_ROOT, validator.sys.executable, command, "offline"
        )
        self.assertEqual(record["exitCode"], 7)
        self.assertFalse(record["passed"])
        self.assertEqual(len(record["stdoutSha256"]), 64)

    def test_source_identity_changes_with_bound_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "value.txt"
            source.write_text("first", encoding="utf-8")
            before = validator.source_identity(root)
            source.write_text("second", encoding="utf-8")
            after = validator.source_identity(root)
        self.assertNotEqual(before["sha256"], after["sha256"])


if __name__ == "__main__":
    unittest.main()
