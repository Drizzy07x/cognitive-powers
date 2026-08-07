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
    PLUGIN_ROOT / "skills" / "solve-efficiently" / "scripts" / "semantic_context.py"
)


def load_semantic_context():
    spec = importlib.util.spec_from_file_location(
        "test_semantic_context_module", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


semantic_context = load_semantic_context()


def fresh_status(**overrides: object) -> dict[str, object]:
    status: dict[str, object] = {
        "initialized": True,
        "version": "1.4.1",
        "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
        "worktreeMismatch": None,
        "index": {
            "state": "complete",
            "pendingRefs": 0,
            "reindexRecommended": False,
        },
    }
    status.update(overrides)
    return status


class FakeRunner:
    def __init__(self, responses: list[tuple[int, str, str]]) -> None:
        self.responses = list(responses)
        self.calls: list[list[str]] = []

    def __call__(
        self, command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        returncode, stdout, stderr = self.responses.pop(0)
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)


class SemanticContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "src" / "policy.py").write_text(
            "def should_retry(code):\n    return code == 503\n",
            encoding="utf-8",
        )
        (self.root / "tests" / "test_policy.py").write_text(
            "from src.policy import should_retry\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_missing_codegraph_falls_back_without_claiming_semantics(self) -> None:
        result = semantic_context.explore(
            self.root,
            "retry policy",
            executable="missing-codegraph-cognitive-powers-test",
            max_chars=500,
        )

        self.assertEqual(result["provider"], "context-lens")
        self.assertFalse(result["semantic"])
        self.assertIn("executable not found", result["fallback_reason"])
        self.assertTrue(result["files"])

    def test_fresh_index_returns_bounded_semantic_context(self) -> None:
        source = "call path\n" + ("x" * 200)
        runner = FakeRunner(
            [
                (0, json.dumps(fresh_status()), ""),
                (0, source, ""),
            ]
        )

        result = semantic_context.explore(
            self.root,
            "how does retry reach the gateway",
            executable=sys.executable,
            max_files=4,
            max_chars=80,
            runner=runner,
        )

        self.assertEqual(result["provider"], "codegraph")
        self.assertTrue(result["semantic"])
        self.assertTrue(result["truncated"])
        self.assertLessEqual(result["payload_chars"], 80)
        self.assertEqual(runner.calls[0][1], "status")
        self.assertEqual(runner.calls[1][1], "explore")
        self.assertIn("--max-files", runner.calls[1])

    def test_pending_changes_force_lexical_fallback(self) -> None:
        status = fresh_status(pendingChanges={"added": 0, "modified": 1, "removed": 0})
        runner = FakeRunner([(0, json.dumps(status), "")])

        result = semantic_context.explore(
            self.root,
            "retry policy",
            executable=sys.executable,
            runner=runner,
        )

        self.assertEqual(result["provider"], "context-lens")
        self.assertIn("pending source changes", result["fallback_reason"])
        self.assertEqual(len(runner.calls), 1)

    def test_a_counter_it_cannot_read_makes_the_index_unusable(self) -> None:
        """Freshness is the one thing this probe exists to decide.

        Counters were summed only when they were already ``int``, so a status
        saying ``{"modified": "2"}`` -- two files changed -- filtered itself out
        of the total and the index was reported clean and usable. Every shape
        the reader did not recognize resolved the same way, which trusts a
        provider exactly when it can no longer be understood.
        """
        unreadable = (
            fresh_status(pendingChanges={"added": 0, "modified": "2", "removed": 0}),
            fresh_status(pendingChanges={"added": 0, "modified": 2.0, "removed": 0}),
            fresh_status(pendingChanges=[{"modified": 2}]),
            fresh_status(pendingChanges=7),
            fresh_status(index={"state": "complete", "pendingRefs": "9"}),
            fresh_status(index=[{"state": "broken"}]),
        )
        for status in unreadable:
            with self.subTest(status=status):
                runner = FakeRunner([(0, json.dumps(status), "")])

                probe = semantic_context.probe_codegraph(
                    self.root, executable=sys.executable, runner=runner
                )

                self.assertFalse(probe["usable"], probe["reason"])
                self.assertTrue(probe["warnings"])

    def test_a_status_that_omits_a_counter_is_not_accused(self) -> None:
        """An older CLI that never emits the field is silent, not suspect."""
        for status in (
            {"initialized": True, "index": {"state": "complete", "pendingRefs": 0}},
            {
                "initialized": True,
                "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
            },
        ):
            with self.subTest(status=status):
                runner = FakeRunner([(0, json.dumps(status), "")])

                probe = semantic_context.probe_codegraph(
                    self.root, executable=sys.executable, runner=runner
                )

                self.assertTrue(probe["usable"], probe["reason"])

    def test_impact_normalizes_graph_result(self) -> None:
        raw = {
            "symbol": "should_retry",
            "depth": 2,
            "nodeCount": 1,
            "edgeCount": 1,
            "affected": [
                {
                    "name": "retry_delay",
                    "kind": "method",
                    "filePath": "src/client.py",
                    "startLine": 8,
                }
            ],
        }
        runner = FakeRunner(
            [(0, json.dumps(fresh_status()), ""), (0, json.dumps(raw), "")]
        )

        result = semantic_context.impact(
            self.root,
            "should_retry",
            executable=sys.executable,
            runner=runner,
        )

        self.assertTrue(result["complete"])
        self.assertEqual(result["affected"][0]["filePath"], "src/client.py")
        self.assertIn("--json", runner.calls[1])

    def test_affected_tests_are_candidates_not_execution_evidence(self) -> None:
        raw = {
            "changedFiles": ["src/policy.py"],
            "affectedTests": ["tests/test_policy.py"],
            "totalDependentsTraversed": 2,
        }
        runner = FakeRunner(
            [(0, json.dumps(fresh_status()), ""), (0, json.dumps(raw), "")]
        )

        result = semantic_context.affected_tests(
            self.root,
            ["src/policy.py"],
            executable=sys.executable,
            runner=runner,
        )

        self.assertTrue(result["semantic"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["affectedTests"], ["tests/test_policy.py"])
        self.assertEqual(result["testFilter"], "**/test_*.py")
        self.assertIn("--filter", runner.calls[1])
        self.assertNotIn("verified", result)


if __name__ == "__main__":
    unittest.main()
