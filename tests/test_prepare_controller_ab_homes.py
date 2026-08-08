from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import prepare_controller_ab_homes as homes  # noqa: E402


class PrepareControllerAbHomesTests(unittest.TestCase):
    @staticmethod
    def _write_runtime_source(source: Path) -> None:
        for relative in homes.INSTALLED_SURFACE_DIRECTORIES:
            directory = source / relative
            directory.mkdir(parents=True)
            (directory / "runtime.txt").write_text(
                f"runtime:{relative}\n", encoding="utf-8"
            )
        for relative in homes.INSTALLED_SURFACE_FILES:
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"runtime:{relative}\n", encoding="utf-8")

    def test_minimal_config_enables_only_cognitive_powers(self) -> None:
        config = homes._minimal_config("gpt-test", "medium")
        self.assertIn('[plugins."cognitive-powers@personal"]', config)
        self.assertNotIn("context-mode", config)
        self.assertNotIn("openai-bundled", config)
        self.assertIn("memories = false", config)
        self.assertIn("multi_agent = true", config)

    def test_dirty_source_is_rejected(self) -> None:
        completed = mock.Mock(returncode=0, stdout="abc\n", stderr="")
        dirty = mock.Mock(returncode=0, stdout=" M file.py\n", stderr="")
        with mock.patch.object(homes.subprocess, "run", side_effect=[completed, dirty]):
            with self.assertRaisesRegex(homes.HomePreparationError, "must be clean"):
                homes._git_identity(Path("."))

    def test_copy_plugin_excludes_runtime_and_git_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            self._write_runtime_source(source)
            (source / ".git").mkdir(parents=True)
            (source / "__pycache__").mkdir()
            (source / ".git" / "HEAD").write_text("ref", encoding="utf-8")
            (source / "__pycache__" / "x.pyc").write_bytes(b"cache")
            surface = homes._copy_plugin(source, destination)
            self.assertTrue(
                (destination / "scripts" / "orchestration_policy.py").is_file()
            )
            self.assertFalse((destination / ".git").exists())
            self.assertFalse((destination / "__pycache__").exists())
            self.assertEqual(
                surface["sha256"], homes.source_sha256(homes.tree_hashes(destination))
            )

    def test_copy_plugin_excludes_confirmatory_and_evaluator_material(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            self._write_runtime_source(source)
            (source / "benchmarks").mkdir()
            (source / "benchmarks" / "evaluation_tasks.json").write_text(
                '{"expected_mode":"parallel-packets"}\n', encoding="utf-8"
            )
            (source / "tests").mkdir()
            (source / "tests" / "test_secret.py").write_text(
                "EXPECTED_MODE = 'parallel-packets'\n", encoding="utf-8"
            )
            (source / "scripts" / "live_ab_runner.py").write_text(
                "EXPECTED_MODE = 'parallel-packets'\n", encoding="utf-8"
            )

            surface = homes._copy_plugin(source, destination)

            self.assertFalse((destination / "benchmarks").exists())
            self.assertFalse((destination / "tests").exists())
            self.assertFalse((destination / "scripts" / "live_ab_runner.py").exists())
            self.assertEqual(
                surface["excluded_development_paths"],
                list(homes.SENSITIVE_DEVELOPMENT_PATHS),
            )

    def test_copy_plugin_requires_complete_runtime_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            with self.assertRaisesRegex(
                homes.HomePreparationError, "runtime surface directory is missing"
            ):
                homes._copy_plugin(source, root / "destination")

    def test_copy_plugin_preflights_budgets_before_creating_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            self._write_runtime_source(source)
            with self.assertRaisesRegex(
                homes.HomePreparationError, "file count.*budget"
            ):
                homes._copy_plugin(
                    source,
                    destination,
                    max_files=1,
                    max_bytes=1_000_000,
                )
            self.assertFalse(destination.exists())

    def test_copy_plugin_rejects_large_excluded_dependency_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            self._write_runtime_source(source)
            dependencies = source / "skills" / "node_modules"
            dependencies.mkdir()
            for index in range(3):
                (dependencies / f"{index}.js").write_text(
                    "dependency", encoding="utf-8"
                )
            with self.assertRaisesRegex(
                homes.HomePreparationError,
                r"excluded large tree.*node_modules.*override",
            ):
                homes._copy_plugin(
                    source,
                    root / "rejected",
                    large_tree_file_limit=2,
                )
            surface = homes._copy_plugin(
                source,
                root / "allowed",
                allow_large_excluded_trees=True,
                large_tree_file_limit=2,
            )
            self.assertGreater(surface["file_count"], 0)
            self.assertFalse((root / "allowed" / "skills" / "node_modules").exists())

    def test_login_must_be_chatgpt(self) -> None:
        completed = mock.Mock(
            returncode=0, stdout="Logged in using API key\n", stderr=""
        )
        with (
            mock.patch.object(homes, "resolve_codex_executable", return_value="codex"),
            mock.patch.object(homes.subprocess, "run", return_value=completed),
        ):
            with self.assertRaisesRegex(homes.HomePreparationError, "ChatGPT"):
                homes._login_status("codex", Path("home"))

    def test_missing_codex_cli_is_a_home_preparation_error(self) -> None:
        # A bare name reached CreateProcess, which only appends .exe, so a
        # missing or npm-shimmed CLI surfaced as a raw WinError 2 traceback
        # instead of a preparation diagnostic.
        with self.assertRaisesRegex(homes.HomePreparationError, "cp-absent-codex-cli"):
            homes._login_status("cp-absent-codex-cli", Path("home"))

    def test_unlaunchable_codex_cli_is_a_home_preparation_error(self) -> None:
        with (
            mock.patch.object(homes, "resolve_codex_executable", return_value="codex"),
            mock.patch.object(homes.subprocess, "run", side_effect=OSError("blocked")),
        ):
            with self.assertRaisesRegex(
                homes.HomePreparationError, "cannot execute the Codex CLI"
            ):
                homes._login_status("codex", Path("home"))

    def test_prepared_homes_validate_against_explicit_plugin_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_home = root / "source-home"
            plugin_source = root / "source"
            output = root / "homes"
            source_home.mkdir()
            (source_home / "auth.json").write_text("{}\n", encoding="utf-8")
            (source_home / "AGENTS.md").write_text("test\n", encoding="utf-8")
            self._write_runtime_source(plugin_source)
            manifest = plugin_source / ".codex-plugin" / "plugin.json"
            manifest.write_text('{"version":"1.0"}\n', encoding="utf-8")
            source_git = {
                "head": "a" * 40,
                "status_sha256": "b" * 64,
                "sha256": "c" * 64,
            }
            plugin_identity = {"source_sha256": "d" * 64}
            host_identity = {"version": "codex-test"}

            issued: list[tuple[str, tuple[str, ...]]] = []

            def fake_codex(codex: str, home, *arguments: str) -> str:
                issued.append((home.name, arguments))
                if arguments[:2] == ("plugin", "add"):
                    # Stand in for what the CLI does on a successful install.
                    (
                        home
                        / "plugins"
                        / "cache"
                        / homes.MARKETPLACE_NAME
                        / homes.PLUGIN_NAME
                        / "1.0"
                    ).mkdir(parents=True)
                return ""

            with (
                mock.patch.object(homes, "_git_identity", return_value=source_git),
                mock.patch.object(homes, "_login_status", return_value="chatgpt"),
                mock.patch.object(homes, "_run_codex", side_effect=fake_codex),
                mock.patch.object(
                    homes,
                    "validate_arm_plugins",
                    return_value=plugin_identity,
                ) as validate,
                mock.patch.object(
                    homes, "codex_host_identity", return_value=host_identity
                ),
            ):
                receipt = homes.prepare_homes(
                    source_home=source_home,
                    plugin_source=plugin_source,
                    output_root=output,
                    model="gpt-test",
                    reasoning_effort="medium",
                    codex="codex",
                )

            validate.assert_called_once_with(
                "codex",
                (output / "baseline").resolve(),
                (output / "candidate").resolve(),
                canonical_source=plugin_source.resolve(),
            )
            self.assertEqual(receipt["source_git"], source_git)
            # The preparation used to build plugins/cache/... and a config entry
            # by hand. Codex resolves installations from marketplace snapshots,
            # so that home reported no marketplaces and no plugins and the arms
            # could never validate. Pin the commands, not just the outcome:
            # nothing else in this suite can reach the real CLI.
            marketplace = str(output.resolve() / homes.MARKETPLACE_DIRECTORY)
            for arm in ("baseline", "candidate"):
                self.assertIn(
                    (arm, ("plugin", "marketplace", "add", marketplace)), issued
                )
                self.assertIn(
                    (
                        arm,
                        (
                            "plugin",
                            "add",
                            f"{homes.PLUGIN_NAME}@{homes.MARKETPLACE_NAME}",
                        ),
                    ),
                    issued,
                )

    def test_both_arms_install_from_one_marketplace(self) -> None:
        """Two marketplace roots would be two chances for the arms to differ."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "marketplace" / homes.PLUGIN_PAYLOAD_DIRECTORY
            payload.mkdir(parents=True)
            homes._write_marketplace(payload, root / "marketplace")
            manifest = json.loads(
                (
                    root / "marketplace" / ".agents" / "plugins" / "marketplace.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(manifest["name"], homes.MARKETPLACE_NAME)
        entry = manifest["plugins"][0]
        self.assertEqual(entry["name"], homes.PLUGIN_NAME)
        # The payload sits in a subdirectory so the tree Codex installs is the
        # runtime surface alone; a marketplace rooted at the plugin itself would
        # install its own manifest alongside it.
        self.assertEqual(
            entry["source"], {"source": "local", "path": f"./{payload.name}"}
        )

    def test_an_install_that_produces_no_cache_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_home = root / "source-home"
            plugin_source = root / "source"
            source_home.mkdir()
            (source_home / "auth.json").write_text("{}\n", encoding="utf-8")
            (source_home / "AGENTS.md").write_text("test\n", encoding="utf-8")
            self._write_runtime_source(plugin_source)
            (plugin_source / ".codex-plugin" / "plugin.json").write_text(
                '{"version":"1.0"}\n', encoding="utf-8"
            )

            with (
                mock.patch.object(
                    homes,
                    "_git_identity",
                    return_value={"head": "a" * 40, "status_sha256": "b" * 64},
                ),
                mock.patch.object(homes, "_run_codex", return_value=""),
                self.assertRaisesRegex(homes.HomePreparationError, "did not install"),
            ):
                homes.prepare_homes(
                    source_home=source_home,
                    plugin_source=plugin_source,
                    output_root=root / "homes",
                    model="gpt-test",
                    reasoning_effort="medium",
                    codex="codex",
                )


class InstalledSurfaceHookExecutionTests(unittest.TestCase):
    """Run every registered hook from a real copy of the installed surface.

    The surface constants used to be validated only against themselves: the
    list was frozen before the hooks that import ``scripts/skill_routing.py``
    and ``scripts/plugin_host.py`` landed, and those hooks degrade to silence
    on ImportError, so prepared homes shipped with skill routing disabled and
    every packaging check stayed green. Executing the registered hooks from a
    surface copy is what makes a missing hook dependency a red test.
    """

    @staticmethod
    def _registered_codex_hooks(copy: Path) -> list[tuple[str, str, list[str]]]:
        """Read the registrations the prepared homes actually run."""
        registrations = json.loads(
            (copy / "hooks" / "hooks.codex.json").read_text(encoding="utf-8")
        )
        hooks: list[tuple[str, str, list[str]]] = []
        for groups in registrations["hooks"].values():
            for group in groups:
                for hook in group["hooks"]:
                    argv = shlex.split(hook["command"])
                    script = argv[1].replace("$PLUGIN_ROOT", str(copy))
                    hooks.append(
                        (
                            Path(script).name,
                            argv[2],
                            [sys.executable, script, *argv[2:]],
                        )
                    )
        return hooks

    @staticmethod
    def _minimal_payloads(
        workspace: Path, edited: Path
    ) -> dict[tuple[str, str], dict[str, object]]:
        return {
            ("semantic_index.py", "session-start"): {
                "source": "startup",
                "cwd": str(workspace),
            },
            ("skill_activation.py", "session-start"): {"source": "startup"},
            ("skill_router.py", "user-prompt-submit"): {
                "user_input": "help me refactor this module cleanly"
            },
            ("selective_hooks.py", "post-tool-use"): {
                "sessionId": "cp-surface-probe",
                "tool_name": "Write",
                "tool_input": {"file_path": str(edited)},
                "cwd": str(workspace),
            },
            ("selective_hooks.py", "stop"): {
                "sessionId": "cp-surface-probe-untouched",
                "cwd": str(workspace),
            },
        }

    @staticmethod
    def _hook_environment(copy: Path, store: Path) -> dict[str, str]:
        environment = dict(os.environ)
        environment["PLUGIN_ROOT"] = str(copy)
        environment["COGNITIVE_POWERS_DATA"] = str(store)
        for variable in (
            "CLAUDE_PLUGIN_ROOT",
            "COGNITIVE_POWERS_DISABLE_ACTIVATION",
            "COGNITIVE_POWERS_DISABLE_ROUTER",
            "COGNITIVE_POWERS_DISABLE_INDEX",
            "COGNITIVE_POWERS_ENABLE_ACTIVATION_INDEX",
        ):
            environment.pop(variable, None)
        return environment

    def test_registered_hooks_do_not_degrade_on_the_copied_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy = root / "installed"
            workspace = root / "workspace"
            store = root / "store"
            workspace.mkdir()
            edited = workspace / "edited.py"
            edited.write_text("VALUE = 1\n", encoding="utf-8")
            homes._copy_plugin(ROOT, copy)
            payloads = self._minimal_payloads(workspace, edited)
            environment = self._hook_environment(copy, store)
            outputs: dict[tuple[str, str], str] = {}
            for name, event, command in self._registered_codex_hooks(copy):
                completed = subprocess.run(
                    command,
                    input=json.dumps(payloads[(name, event)]),
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=str(workspace),
                    env=environment,
                    timeout=120,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                outputs[(name, event)] = completed.stdout
            # Both directions: every registration is judged, and a registration
            # this test expected cannot silently disappear.
            self.assertEqual(set(outputs), set(payloads))
            # The routing hooks degrade to silence, so their healthy injected
            # context is the only observable proof the copied surface carries
            # the scripts they import.
            for key in (
                ("skill_activation.py", "session-start"),
                ("skill_router.py", "user-prompt-submit"),
            ):
                injected = json.loads(outputs[key])
                self.assertTrue(
                    injected["hookSpecificOutput"]["additionalContext"].strip(), key
                )
            # A partially parsable catalogue warns through systemMessage; the
            # real surface must not.
            self.assertNotIn(
                "systemMessage",
                json.loads(outputs[("skill_router.py", "user-prompt-submit")]),
            )
            # These two report degradation as output: a startup failure line,
            # and a Stop warning for an untouched session's ledger.
            self.assertEqual(outputs[("semantic_index.py", "session-start")], "")
            self.assertEqual(outputs[("selective_hooks.py", "stop")], "")
            # The edit event must land in the ledger the Stop gate reads; a
            # dropped event is that hook's silent degradation.
            self.assertTrue(list((store / "hooks" / "events").glob("*.jsonl")))


if __name__ == "__main__":
    unittest.main()
