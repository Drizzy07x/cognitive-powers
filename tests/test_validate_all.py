from __future__ import annotations

import importlib
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
    def test_node_modules_do_not_change_receipt_or_witness_source_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "source.py").write_text("value = 1\n", encoding="utf-8")
            before = validator.source_identity(root)
            witness_before = [
                path.relative_to(root)
                for path in release_witness.iter_release_files(root)
            ]
            dependency = root / "ci" / "host" / "node_modules" / "package"
            dependency.mkdir(parents=True)
            (dependency / "index.js").write_text("noise\n", encoding="utf-8")
            self.assertEqual(validator.source_identity(root), before)
            self.assertEqual(
                [
                    path.relative_to(root)
                    for path in release_witness.iter_release_files(root)
                ],
                witness_before,
            )

    def test_verify_installed_gate_uses_only_an_isolated_fixture_home(self) -> None:
        command = next(
            item
            for item in validator.OFFLINE_COMMANDS
            if item.name == "verify-installed-fixture"
        )
        self.assertEqual(
            command.argv, ("tests/fixtures/run_verify_installed_fixture.py",)
        )
        fixture = (PLUGIN_ROOT / command.argv[0]).read_text(encoding="utf-8")
        for variable in ("CODEX_HOME", "HOME", "USERPROFILE"):
            self.assertIn(variable, fixture)
        self.assertIn("rev-parse", fixture)
        self.assertIn("^{{commit}}", fixture)
        self.assertIn("json.loads", fixture)

        workflow = (PLUGIN_ROOT / ".github" / "workflows" / "validate.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("verify-installed-fixture", workflow)

    def test_targeted_unittest_modules_are_importable(self) -> None:
        targeted_modules = next(
            command.argv[2:]
            for command in validator.OFFLINE_COMMANDS
            if command.name == "controller-ab-targeted-tests"
        )
        self.assertTrue(targeted_modules)
        for module_name in targeted_modules:
            with self.subTest(module=module_name):
                self.assertEqual(
                    importlib.import_module(module_name).__name__, module_name
                )

    def test_ci_installs_pinned_validation_dependencies(self) -> None:
        requirements_path = PLUGIN_ROOT / "requirements-dev.in"
        requirements = requirements_path.read_text(encoding="utf-8").splitlines()
        declared = [
            line.strip()
            for line in requirements
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(declared, ["ruff==0.16.1"])

        workflow = (PLUGIN_ROOT / ".github" / "workflows" / "validate.yml").read_text(
            encoding="utf-8"
        )
        install_command = (
            "python -m pip install --require-hashes -r requirements-dev.txt"
        )
        self.assertIn(install_command, workflow)
        self.assertLess(
            workflow.index(install_command),
            workflow.index("python scripts/validate_all.py --offline"),
        )

        evidence = (PLUGIN_ROOT / "docs" / "evidence.md").read_text(encoding="utf-8")
        self.assertIn(
            "& $python -m pip install --require-hashes -r requirements-dev.txt",
            evidence,
        )

    def test_ci_covers_cross_platform_lock_and_canonical_validation(self) -> None:
        workflow = (PLUGIN_ROOT / ".github" / "workflows" / "validate.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "os: [ubuntu-latest, windows-latest, macos-latest]",
            workflow,
        )
        lock_step = workflow.index("- name: Exercise the cross-platform hook lock")
        canonical_step = workflow.index(
            "- name: Run the canonical offline validation entrypoint"
        )
        self.assertLess(lock_step, canonical_step)
        lock_slice = workflow[lock_step:canonical_step]
        self.assertIn(
            "python -m unittest "
            "tests.test_plugin_hooks.PluginHookTests."
            "test_short_lock_contention_does_not_silently_drop_event",
            lock_slice,
        )
        self.assertIn(
            "tests.test_plugin_hooks.PluginHookTests."
            "test_unlocked_residual_lock_file_does_not_block_event",
            lock_slice,
        )
        self.assertNotIn("secrets.", workflow)
        for release_command in ("gh release", "npm publish"):
            with self.subTest(command=release_command):
                self.assertNotIn(release_command, workflow)
        install_step = workflow.index(
            "- name: Install and verify the release ref in a disposable Codex home"
        )
        release_step = workflow.index("- name: Build release manifest (first pass)")
        install_slice = workflow[install_step:release_step]
        self.assertIn("startsWith(github.ref, 'refs/tags/')", install_slice)
        self.assertLess(
            install_slice.index("$env:CODEX_HOME"), install_slice.index("codex plugin")
        )
        self.assertLess(
            install_slice.index("$env:HOME"), install_slice.index("codex plugin")
        )
        self.assertLess(
            install_slice.index("$env:USERPROFILE"), install_slice.index("codex plugin")
        )
        # Codex refuses to load a configuration when CODEX_HOME names a path
        # that does not exist, so naming the disposable home is not enough: it
        # has to exist before the installer runs any codex command.
        self.assertLess(
            install_slice.index(
                "New-Item -ItemType Directory -Force -Path $env:CODEX_HOME"
            ),
            install_slice.index("./install.ps1"),
        )

    def test_ci_resolves_the_codex_executable_before_running_it(self) -> None:
        # The lockfile-backed CLI is an npm shim, so on Windows it is codex.cmd.
        # CreateProcess only appends .exe to a bare name, so handing 'codex' to
        # subprocess raised WinError 2 on every Windows cell while the pwsh
        # steps around it ran the same command without trouble.
        workflow = (PLUGIN_ROOT / ".github" / "workflows" / "validate.yml").read_text(
            encoding="utf-8"
        )
        step = workflow.index("- name: Resolve exact Codex CLI version")
        following = workflow.index("- name: ", step + 1)
        step_slice = workflow[step:following]
        self.assertIn("shutil.which('codex')", step_slice)
        self.assertNotIn("subprocess.run(['codex'", step_slice)

    def test_aggregation_counts_receipts_not_their_scenario_evidence(self) -> None:
        # Every cell uploads compatibility-scenario-evidence.json alongside its
        # nine receipts, and it matches the same glob. Counting it turned the
        # twelve expected cells into 120 files against an expected 108.
        workflow = (PLUGIN_ROOT / ".github" / "workflows" / "validate.yml").read_text(
            encoding="utf-8"
        )
        step = workflow.index("- name: Aggregate receipts from the twelve")
        following = workflow.index("- name: ", step + 1)
        step_slice = workflow[step:following]
        self.assertIn("! -name 'compatibility-scenario-evidence.json'", step_slice)
        self.assertLess(
            step_slice.index("! -name 'compatibility-scenario-evidence.json'"),
            step_slice.index('[ "${#receipts[@]}" -ne 108 ]'),
        )
        # ...and the refusal names what it found instead of failing silently.
        self.assertIn("expected 108 compatibility receipts, found", step_slice)

    def test_release_path_is_exercised_without_a_tag(self) -> None:
        # Nine of the thirteen 1.7.1-era defects lived in steps only a tag
        # push ran. The reproducibility gates now run on every push against a
        # runner-local tag, and the install/upgrade/rollback path runs nightly
        # and on demand against the standing published tag.
        workflow = (PLUGIN_ROOT / ".github" / "workflows" / "validate.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("release_dry_run:", workflow)
        self.assertIn("schedule:", workflow)
        self.assertIn('git tag -f "$tag"', workflow)
        self.assertIn("vars.DRY_RUN_RELEASE_REF", workflow)
        self.assertIn("-ExpectedCommit $expected", workflow)
        self.assertIn("Require the exact publishable asset shape", workflow)
        # The manifest reproducibility steps carry no tag gate any more.
        first_pass = workflow.index("Build release manifest (first pass)")
        compare = workflow.index("name: compare-release-builds")
        self.assertNotIn(
            "if: startsWith(github.ref, 'refs/tags/')",
            workflow[first_pass:compare],
        )
        # The commit-bound steps stay tag-only by design.
        receipts_step = workflow.index("Produce evidence-bound compatibility receipts")
        upload_step = workflow.index("Preserve validation receipt")
        self.assertIn(
            "startsWith(github.ref, 'refs/tags/')",
            workflow[receipts_step:upload_step],
        )

    def test_receipts_record_skipped_tests(self) -> None:
        # A skipped test is an assertion that did not run; without the list a
        # Windows cell without symlink rights reports "compatible" while the
        # symlink tests never executed, indistinguishable from full coverage.
        stderr = (
            b"test_a (tests.x.T.test_a) ... skipped 'symlinks unavailable'\n"
            b"test_b (tests.x.T.test_b) ... ok\n"
        )
        skipped = validator._skipped_tests(stderr)
        self.assertEqual(
            skipped,
            [
                {
                    "test": "test_a (tests.x.T.test_a)",
                    "reason": "symlinks unavailable",
                }
            ],
        )
        self.assertEqual(validator._skipped_tests(b"all ok\n"), [])

    def test_release_evidence_artifact_is_flat(self) -> None:
        # upload-artifact preserves each path relative to the least common
        # ancestor of all of them, and the publisher lists assets at a depth of
        # one and attaches them with a flat glob. A path outside release-final
        # therefore buries every other asset one directory down and the exact
        # asset allowlist can never match.
        workflow = (PLUGIN_ROOT / ".github" / "workflows" / "validate.yml").read_text(
            encoding="utf-8"
        )
        # Constant artifact name: a ref-suffixed name cannot exist for branch
        # refs (they contain "/") and the publisher binds run and tag itself.
        self.assertNotIn("release-evidence-${{", workflow)
        step = workflow.index("name: release-evidence")
        following = workflow.index("if-no-files-found: error", step)
        paths = [
            line.strip()
            for line in workflow[step:following].splitlines()
            if line.strip().startswith("${{ runner.temp }}")
        ]
        self.assertEqual(len(paths), 7)
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue(path.startswith("${{ runner.temp }}/release-final/"))

    def test_ci_keeps_validation_separate_from_receipt_publication(self) -> None:
        workflow = (PLUGIN_ROOT / ".github" / "workflows" / "validate.yml").read_text(
            encoding="utf-8"
        )
        validation_step = workflow.index(
            "- name: Run the canonical offline validation entrypoint"
        )
        receipt_report_step = workflow.index("- name: Report validation receipt")
        upload_step = workflow.index("- name: Preserve validation receipt")
        summary_step = workflow.index(
            "- name: Report validation and artifact publication status"
        )
        self.assertLess(validation_step, receipt_report_step)
        self.assertLess(receipt_report_step, upload_step)
        self.assertLess(upload_step, summary_step)
        self.assertNotIn(
            "continue-on-error: true",
            workflow[validation_step:receipt_report_step],
        )
        self.assertIn(
            "id: receipt-report",
            workflow[receipt_report_step:upload_step],
        )
        self.assertIn(
            "if: always()",
            workflow[receipt_report_step:upload_step],
        )
        self.assertIn(
            "python scripts/report_validation_receipt.py",
            workflow[receipt_report_step:upload_step],
        )
        self.assertIn(
            "continue-on-error: true",
            workflow[upload_step:summary_step],
        )
        self.assertIn(
            "steps.receipt-report.outcome == 'success'",
            workflow[upload_step:summary_step],
        )
        self.assertIn("steps.receipt-upload.outcome", workflow[summary_step:])
        self.assertIn("--publication-outcome", workflow[summary_step:])

        evidence = (PLUGIN_ROOT / "docs" / "evidence.md").read_text(encoding="utf-8")
        self.assertIn("receipt_uploaded=false", evidence)
        self.assertIn("does not mean release-ready", evidence)

    def test_release_witness_uses_same_offline_command_contract(self) -> None:
        signature = [
            (command.name, command.argv) for command in validator.OFFLINE_COMMANDS
        ]
        self.assertEqual(signature, list(release_witness.EXPECTED_OFFLINE_COMMANDS))

    def test_documented_offline_list_matches_orchestrator(self) -> None:
        """The published command list has to be the list that actually runs.

        It lived in the README until the README became a reading path; the
        gate follows the content to docs/evidence.md rather than pinning the
        prose to the file it happened to start in.
        """
        lines = (
            (PLUGIN_ROOT / "docs" / "evidence.md")
            .read_text(encoding="utf-8")
            .splitlines()
        )
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
                return_value={
                    "sha256": "b" * 64,
                    "fileCount": 10,
                    "algorithm": validator.SOURCE_IDENTITY_ALGORITHM,
                },
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
                return_value={
                    "sha256": "b" * 64,
                    "fileCount": 10,
                    "algorithm": validator.SOURCE_IDENTITY_ALGORITHM,
                },
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

    def test_failed_command_is_exposed_in_console_summary(self) -> None:
        failed = command_result(
            validator.OFFLINE_COMMANDS[2],
            "offline",
            passed=False,
        )
        failed["stdoutTail"] = "FAILED: runner-specific assertion"
        failed["stderrTail"] = "runner stderr"
        payload = {
            "passed": False,
            "offline": {"passed": False},
            "live": {"validated": False},
            "commands": [failed],
        }
        summary = validator.console_summary(payload, Path("receipt.json"))
        self.assertEqual(summary["failedCommands"][0]["name"], failed["name"])
        self.assertEqual(summary["failedCommands"][0]["command"], failed["command"])
        self.assertEqual(summary["failedCommands"][0]["exitCode"], 7)
        self.assertEqual(
            summary["failedCommands"][0]["stdoutTail"],
            "FAILED: runner-specific assertion",
        )
        self.assertEqual(summary["failedCommands"][0]["stderrTail"], "runner stderr")

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

    def test_source_identity_uses_shared_dependency_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source.py").write_text("kept\n", encoding="utf-8")
            before = validator.source_identity(root)
            for name in ("node_modules", ".next", "build", "coverage", "storage"):
                tree = root / name
                tree.mkdir()
                (tree / "ignored.bin").write_bytes(b"generated")
            after = validator.source_identity(root)

        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
