from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "aggregate_release_artifacts.py"
IDENTITY = ROOT / "scripts" / "release_identity.py"


def load_module(path: Path = SCRIPT, name: str = "aggregate_release_artifacts_test"):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def declared_version() -> str:
    return load_module(IDENTITY, "release_identity_test").plugin_version()


class AggregateReleaseArtifactsTests(unittest.TestCase):
    def _write_cell(
        self,
        root: Path,
        index: int,
        archive: bytes = b"release",
        version: str | None = "",
    ) -> None:
        cell = root / f"cell-{index}"
        cell.mkdir()
        digest = hashlib.sha256(archive).hexdigest()
        # Shaped like what build_release_manifest.py actually emits: the version
        # is carried by the manifest, so the aggregate names the archive after
        # the content it verified rather than after a literal.
        manifest: dict[str, object] = {
            "schemaVersion": 1,
            "product": "cognitive-powers",
            "archive": {"sha256": digest},
        }
        if version is not None:
            manifest["version"] = declared_version() if version == "" else version
        (cell / "release-one.tar").write_bytes(archive)
        (cell / "release-one.json").write_text(
            json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
        )
        (cell / "release-one.sha256").write_text(
            f"{digest}  release-one.tar\n", encoding="ascii"
        )

    def _source(self, root: Path, **cell: object) -> Path:
        source = root / "source"
        source.mkdir()
        for index in range(12):
            self._write_cell(source, index, **cell)
        return source

    def test_requires_all_cells_and_emits_one_canonical_verified_set(self) -> None:
        module = load_module()
        version = declared_version()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            output = root / "output"
            report = module.aggregate(self._source(root), output, expected_cells=12)
            self.assertTrue(report["byteIdentical"])
            self.assertEqual(report["verifiedCells"], 12)
            self.assertEqual(report["version"], version)
            self.assertEqual(report["archive"], f"cognitive-powers-{version}.tar")
            self.assertTrue((output / f"cognitive-powers-{version}.tar").is_file())
            self.assertTrue(
                (output / f"cognitive-powers-{version}.tar.sha256").is_file()
            )
            self.assertTrue((output / "release-manifest.json").is_file())
            report_payload = json.loads(
                (output / "reproducibility-report.json").read_text(encoding="utf-8")
            )
            # The report is a published asset: paths in it are spelled
            # platform-independently, never with the producing OS separator.
            for record in report_payload["artifacts"]:
                self.assertNotIn("\\", record["artifactDirectory"])

    def test_archive_name_follows_the_manifest_rather_than_a_literal(self) -> None:
        """A different release must not ship under this release's filename."""
        module = load_module()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            output = root / "output"
            report = module.aggregate(
                self._source(root, version="9.9.9"), output, expected_cells=12
            )
            self.assertEqual(report["version"], "9.9.9")
            self.assertTrue((output / "cognitive-powers-9.9.9.tar").is_file())
            self.assertTrue((output / "cognitive-powers-9.9.9.tar.sha256").is_file())
            self.assertEqual(
                sorted(path.name for path in output.glob("*.tar")),
                ["cognitive-powers-9.9.9.tar"],
            )

    def test_checksum_names_the_archive_it_covers(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            output = root / "output"
            report = module.aggregate(
                self._source(root, version="9.9.9"), output, expected_cells=12
            )
            recorded = (
                (output / "cognitive-powers-9.9.9.tar.sha256")
                .read_text(encoding="ascii")
                .split()
            )
            self.assertEqual(recorded[0], report["archiveSha256"])
            self.assertEqual(recorded[1], "cognitive-powers-9.9.9.tar")

    def test_rejects_a_manifest_without_a_usable_version(self) -> None:
        module = load_module()
        for version in (None, "1.6", "v1.6.0", ""):
            with self.subTest(version=version):
                with tempfile.TemporaryDirectory() as raw:
                    root = Path(raw)
                    source = root / "source"
                    source.mkdir()
                    for index in range(12):
                        self._write_cell(
                            source,
                            index,
                            version=None if version is None else version or "x",
                        )
                    with self.assertRaises(module.AggregationError):
                        module.aggregate(source, root / "output", expected_cells=12)

    def test_rejects_a_manifest_for_another_product(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            source.mkdir()
            digest = hashlib.sha256(b"release").hexdigest()
            for index in range(12):
                cell = source / f"cell-{index}"
                cell.mkdir()
                (cell / "release-one.tar").write_bytes(b"release")
                (cell / "release-one.json").write_text(
                    json.dumps(
                        {
                            "product": "something-else",
                            "version": "1.0.0",
                            "archive": {"sha256": digest},
                        },
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (cell / "release-one.sha256").write_text(
                    f"{digest}  release-one.tar\n", encoding="ascii"
                )
            with self.assertRaisesRegex(module.AggregationError, "cognitive-powers"):
                module.aggregate(source, root / "output", expected_cells=12)

    def test_rejects_one_divergent_cell(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            source.mkdir()
            for index in range(12):
                self._write_cell(
                    source, index, archive=b"divergent" if index == 11 else b"release"
                )
            with self.assertRaisesRegex(module.AggregationError, "byte-identical"):
                module.aggregate(source, root / "output", expected_cells=12)


if __name__ == "__main__":
    unittest.main()
