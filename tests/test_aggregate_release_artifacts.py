from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "aggregate_release_artifacts.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "aggregate_release_artifacts_test", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AggregateReleaseArtifactsTests(unittest.TestCase):
    def _write_cell(self, root: Path, index: int, archive: bytes = b"release") -> None:
        cell = root / f"cell-{index}"
        cell.mkdir()
        digest = __import__("hashlib").sha256(archive).hexdigest()
        (cell / "release-one.tar").write_bytes(archive)
        (cell / "release-one.json").write_text(
            json.dumps({"archive": {"sha256": digest}}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (cell / "release-one.sha256").write_text(
            f"{digest}  release-one.tar\n", encoding="ascii"
        )

    def test_requires_all_cells_and_emits_one_canonical_verified_set(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            source.mkdir()
            for index in range(12):
                self._write_cell(source, index)
            output = root / "output"
            report = module.aggregate(source, output, expected_cells=12)
            self.assertTrue(report["byteIdentical"])
            self.assertEqual(report["verifiedCells"], 12)
            self.assertTrue((output / "cognitive-powers-1.6.0.tar").is_file())
            self.assertTrue((output / "release-manifest.json").is_file())
            self.assertTrue((output / "reproducibility-report.json").is_file())

    def test_rejects_one_divergent_cell(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            source.mkdir()
            for index in range(12):
                self._write_cell(
                    source, index, b"divergent" if index == 11 else b"release"
                )
            with self.assertRaisesRegex(module.AggregationError, "byte-identical"):
                module.aggregate(source, root / "output", expected_cells=12)


if __name__ == "__main__":
    unittest.main()
