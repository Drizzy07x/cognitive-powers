import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).parents[1] / "skills/solve-efficiently/scripts/semantic_provider.py"
)
spec = importlib.util.spec_from_file_location("semantic_provider", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class SemanticProviderTests(unittest.TestCase):
    def fixture(self, wrong=False, stale=False, links=False):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        (root / "src.py").write_text("def alpha(): pass\n", encoding="utf-8")
        out = root / "graphify-out"
        out.mkdir()
        (out / ".graphify_root").write_text(
            str(root.parent if wrong else root), encoding="utf-8"
        )
        (out / ".graphify_python").write_text(sys.executable, encoding="utf-8")
        digest = hashlib.md5((root / "src.py").read_bytes()).hexdigest()
        digest = "0" * 32 if stale else digest
        (out / "manifest.json").write_text(
            json.dumps({"src.py": {"ast_hash": digest}}), encoding="utf-8"
        )
        graph = {
            "nodes": [
                {
                    "id": "1",
                    "label": "alpha",
                    "source_file": "src.py",
                    "confidence": "EXTRACTED",
                },
                {
                    "id": "2",
                    "label": "caller",
                    "source_file": "test_src.py",
                    "confidence": "INFERRED",
                },
            ],
            "links" if links else "edges": [{"source": "1", "target": "2"}],
        }
        (out / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
        return td, root

    def detector(self, root, *, new=(), deleted=(), returncode=0, error=None):
        files = [str(root / "src.py"), *(str(root / path) for path in new)]
        payload = {
            "files": {"code": files},
            "unchanged_files": {"code": [str(root / "src.py")]},
            "deleted_files": [str(root / path) for path in deleted],
        }

        def run(argv, **kwargs):
            if error is not None:
                raise error
            return subprocess.CompletedProcess(
                argv,
                returncode,
                stdout=json.dumps(payload) if returncode == 0 else "",
                stderr="detector failed" if returncode else "",
            )

        return run

    def kind_aware_detector(self, root):
        """Answer per requested layer, the way the real provider does.

        The shared ``detector`` above returns one payload whatever ``argv``
        says, so no test could see which question the adapter asks. That blind
        spot is what let the adapter measure a `graphify update` refresh with
        the yardstick for `graphify extract`: an AST-only pass empties
        semantic_hash, so every refreshed file stayed pending for the semantic
        layer while being current for the AST one.
        """
        source = str(root / "src.py")

        def run(argv, **kwargs):
            asks_ast = any(
                "'kind':'ast'" in "".join(str(part).split()) for part in argv
            )
            payload = {
                "files": {"code": [source]},
                "unchanged_files": {"code": [source] if asks_ast else []},
                "deleted_files": [],
            }
            return subprocess.CompletedProcess(
                argv, 0, stdout=json.dumps(payload), stderr=""
            )

        return run

    def test_fresh_graphify_and_normalized_confidence(self):
        td, root = self.fixture()
        self.addCleanup(td.cleanup)
        result = mod.search(
            root,
            "alpha",
            provider="graphify",
            graphify_runner=self.detector(root),
        )
        self.assertEqual("graphify", result["provider"])
        self.assertEqual("high", result["candidates"][0]["confidence"])
        self.assertEqual("navigation_only", result["proof_status"])

    def test_stale_falls_back_to_lexical(self):
        td, root = self.fixture(stale=True)
        self.addCleanup(td.cleanup)
        result = mod.search(
            root,
            "alpha",
            provider="graphify",
            graphify_runner=self.detector(root),
        )
        self.assertEqual("lexical", result["provider"])
        self.assertFalse(result["semantic"])
        self.assertIn("stale", result["reason"])

    def test_wrong_worktree_rejected(self):
        td, root = self.fixture(wrong=True)
        self.addCleanup(td.cleanup)
        probe = mod.probe_graphify(root)
        self.assertFalse(probe["usable"])
        self.assertFalse(probe["worktree_bound"])

    def test_manifest_path_escape_is_rejected_without_crashing(self):
        td, root = self.fixture()
        self.addCleanup(td.cleanup)
        manifest = root / "graphify-out" / "manifest.json"
        manifest.write_text(
            json.dumps({"../outside.py": {"ast_hash": "0" * 32}}),
            encoding="utf-8",
        )

        probe = mod.probe_graphify(root)

        self.assertFalse(probe["usable"])
        self.assertIn("stale", probe["reason"])
        self.assertEqual(["../outside.py"], probe["warnings"])

    def test_affected_are_candidates_not_proof(self):
        td, root = self.fixture()
        self.addCleanup(td.cleanup)
        result = mod.affected(
            root,
            ["src.py"],
            provider="graphify",
            graphify_runner=self.detector(root),
        )
        self.assertGreaterEqual(len(result["candidates"]), 2)
        self.assertTrue(result["requires_behavioral_verification"])

    def test_public_probe_is_bounded_and_supports_networkx_links(self):
        td, root = self.fixture(links=True)
        self.addCleanup(td.cleanup)

        result = mod.probe_graphify(root, runner=self.detector(root))

        self.assertTrue(result["usable"])
        self.assertEqual(result["node_count"], 2)
        self.assertEqual(result["edge_count"], 1)
        self.assertEqual(result["manifest_file_count"], 1)
        self.assertNotIn("graph", result)
        self.assertNotIn("nodes", result)
        self.assertNotIn("links", result)
        self.assertLess(len(json.dumps(result)), 5000)

    def test_duplicate_or_dangling_graph_records_fail_closed(self):
        for name, nodes, edges in (
            (
                "duplicate",
                [{"id": "1"}, {"id": 1}],
                [{"source": "1", "target": "1"}],
            ),
            (
                "dangling",
                [{"id": "1"}],
                [{"source": "1", "target": "missing"}],
            ),
            ("malformed", [{"label": "missing id"}], [{}]),
        ):
            with self.subTest(name=name):
                td, root = self.fixture()
                self.addCleanup(td.cleanup)
                graph = root / "graphify-out" / "graph.json"
                graph.write_text(
                    json.dumps({"nodes": nodes, "links": edges}), encoding="utf-8"
                )

                result = mod.search(
                    root,
                    "alpha",
                    provider="graphify",
                    graphify_runner=self.detector(root),
                )

                self.assertEqual(result["provider"], "lexical")
                self.assertIn("graph", result["reason"])

    def test_incremental_pending_file_forces_lexical_fallback(self):
        td, root = self.fixture()
        self.addCleanup(td.cleanup)
        (root / "pending.md").write_text("pending", encoding="utf-8")

        result = mod.search(
            root,
            "alpha",
            provider="graphify",
            graphify_runner=self.detector(root, new=("pending.md",)),
        )

        self.assertEqual(result["provider"], "lexical")
        self.assertIn("incomplete", result["reason"])

    def test_generated_graphify_memory_does_not_make_index_incomplete(self):
        td, root = self.fixture()
        self.addCleanup(td.cleanup)
        memory = root / "graphify-out" / "memory" / "query.md"
        memory.parent.mkdir()
        memory.write_text("generated", encoding="utf-8")

        probe = mod.probe_graphify(
            root,
            runner=self.detector(root, new=("graphify-out/memory/query.md",)),
        )

        self.assertTrue(probe["usable"])
        self.assertEqual(probe["completeness"]["pending_file_count"], 0)

    def test_completeness_asks_about_the_layer_the_refresh_maintains(self):
        td, root = self.fixture()
        self.addCleanup(td.cleanup)

        probe = mod.probe_graphify(root, runner=self.kind_aware_detector(root))

        self.assertTrue(probe["usable"])
        self.assertEqual("complete", probe["completeness"]["status"])
        self.assertTrue(probe["completeness_verified"])

    def test_detector_failure_and_timeout_fail_closed(self):
        td, root = self.fixture()
        self.addCleanup(td.cleanup)
        runners = (
            self.detector(root, returncode=3),
            self.detector(
                root,
                error=subprocess.TimeoutExpired(["graphify"], timeout=0.01),
            ),
        )
        for runner in runners:
            with self.subTest(runner=runner):
                probe = mod.probe_graphify(root, runner=runner)
                self.assertFalse(probe["usable"])
                self.assertFalse(probe["fresh"])
                self.assertIn("detector", probe["reason"])


if __name__ == "__main__":
    unittest.main()
