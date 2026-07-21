import hashlib
import importlib.util
import json
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
    def fixture(self, wrong=False, stale=False):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        (root / "src.py").write_text("def alpha(): pass\n", encoding="utf-8")
        out = root / "graphify-out"
        out.mkdir()
        (out / ".graphify_root").write_text(
            str(root.parent if wrong else root), encoding="utf-8"
        )
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
            "edges": [{"source": "1", "target": "2"}],
        }
        (out / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
        return td, root

    def test_fresh_graphify_and_normalized_confidence(self):
        td, root = self.fixture()
        self.addCleanup(td.cleanup)
        result = mod.search(root, "alpha", provider="graphify")
        self.assertEqual("graphify", result["provider"])
        self.assertEqual("high", result["candidates"][0]["confidence"])
        self.assertEqual("navigation_only", result["proof_status"])

    def test_stale_falls_back_to_lexical(self):
        td, root = self.fixture(stale=True)
        self.addCleanup(td.cleanup)
        result = mod.search(root, "alpha", provider="graphify")
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
        result = mod.affected(root, ["src.py"], provider="graphify")
        self.assertGreaterEqual(len(result["candidates"]), 2)
        self.assertTrue(result["requires_behavioral_verification"])


if __name__ == "__main__":
    unittest.main()
