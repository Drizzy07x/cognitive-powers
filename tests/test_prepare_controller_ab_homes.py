from __future__ import annotations

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
        with mock.patch.object(homes.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(homes.HomePreparationError, "ChatGPT"):
                homes._login_status("codex", Path("home"))


if __name__ == "__main__":
    unittest.main()
