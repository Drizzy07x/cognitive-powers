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
    PLUGIN_ROOT / "skills" / "solve-efficiently" / "scripts" / "context_lens.py"
)


def load_context_lens():
    spec = importlib.util.spec_from_file_location(
        "test_context_lens_module", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


context_lens = load_context_lens()


class ContextLensTests(unittest.TestCase):
    def test_ignores_dependency_trees_and_ranks_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "src").mkdir()
            (root / "node_modules").mkdir()
            (root / ".codegraph").mkdir()
            (root / ".codegraph-win").mkdir()
            (root / "src" / "retry_policy.py").write_text(
                "exponential retry backoff for gateway failures\n", encoding="utf-8"
            )
            (root / "node_modules" / "noise.js").write_text(
                "retry backoff " * 500, encoding="utf-8"
            )
            (root / ".codegraph" / "metadata.txt").write_text(
                "retry backoff " * 500, encoding="utf-8"
            )
            (root / ".codegraph-win" / "metadata.txt").write_text(
                "retry backoff " * 500, encoding="utf-8"
            )

            result = context_lens.select_context(root, "retry backoff", max_files=5)

            selected_paths = [item["path"] for item in result["files"]]
            self.assertEqual(selected_paths[0], "src/retry_policy.py")
            self.assertFalse(
                any(path.startswith("node_modules/") for path in selected_paths)
            )
            self.assertEqual(result["scanned_files"], 1)

    def test_payload_respects_character_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "large.txt").write_text("needle value " * 1_000, encoding="utf-8")

            result = context_lens.select_context(
                root,
                "needle",
                max_files=1,
                max_chars=60,
                context_lines=0,
            )

            self.assertLessEqual(result["payload_chars"], 60)
            self.assertEqual(result["files"][0]["path"], "large.txt")

    def test_invalid_query_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(ValueError, "searchable term"):
                context_lens.select_context(Path(temporary_directory), "---")

    def test_cli_emits_parseable_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "worker.py").write_text("def retry_job(): pass\n", encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    str(root),
                    "--query",
                    "retry job",
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["files"][0]["path"], "worker.py")

    def test_project_map_recommends_distinct_module_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            module = root / "src" / "payments"
            tests = module / "tests"
            tests.mkdir(parents=True)
            (module / "__init__.py").write_text("", encoding="utf-8")
            (module / "pyproject.toml").write_text(
                "[project]\nname = 'payments'\n", encoding="utf-8"
            )
            for index in range(3):
                (module / f"service_{index}.py").write_text(
                    f"def service_{index}():\n    return {index}\n", encoding="utf-8"
                )
            (tests / "test_service.py").write_text(
                "def test_service():\n    assert True\n", encoding="utf-8"
            )
            (root / "node_modules").mkdir()
            (root / "node_modules" / "noise.js").write_text(
                "export default 1;\n", encoding="utf-8"
            )

            result = context_lens.build_project_map(root, max_depth=3)

            self.assertEqual(result["recommended_locations"][0], ".")
            self.assertIn("src/payments", result["recommended_locations"])
            candidate = next(
                item for item in result["candidates"] if item["path"] == "src/payments"
            )
            self.assertIn("module boundary", candidate["reasons"])
            self.assertIn("code and tests", candidate["reasons"])
            self.assertEqual(result["scanned_files"], 6)

    def test_empty_project_map_is_renderable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = context_lens.build_project_map(Path(temporary_directory))

            rendered = context_lens.format_project_map(result)

            self.assertEqual(result["recommended_locations"], ["."])
            self.assertIn("[0] . - root knowledge base", rendered)

    def test_project_map_cli_emits_parseable_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    temporary_directory,
                    "--project-map",
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["mode"], "project-map")
            self.assertEqual(payload["recommended_locations"], ["."])


if __name__ == "__main__":
    unittest.main()
