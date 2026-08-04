from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts" / "build_release_manifest.py"


def load():
    spec = importlib.util.spec_from_file_location("build_release_manifest", PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ReleaseManifestTests(unittest.TestCase):
    def fixture(self, root: Path) -> None:
        (root / ".codex-plugin").mkdir(parents=True)
        (root / "skills-core" / "solve-efficiently").mkdir(parents=True)
        (root / "skills-core" / "execute-durably").mkdir(parents=True)
        (root / "skills-core" / "verify-delivery").mkdir(parents=True)
        (root / "hooks").mkdir()
        (root / ".codex-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "cognitive-powers",
                    "version": "1.6.0",
                    "skills": "./skills-core/",
                    "hooks": "./hooks/hooks.codex.json",
                }
            ),
            encoding="utf-8",
        )
        for name in ("solve-efficiently", "execute-durably", "verify-delivery"):
            (root / "skills-core" / name / "SKILL.md").write_text(
                name, encoding="utf-8"
            )
        (root / "hooks" / "hooks.codex.json").write_text(
            json.dumps({"hooks": {"Stop": []}}), encoding="utf-8"
        )
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"],
            cwd=root,
            check=True,
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "release"], cwd=root, check=True)
        subprocess.run(["git", "tag", "v1.6.0"], cwd=root, check=True)

    def test_written_manifest_bytes_do_not_depend_on_the_platform(self) -> None:
        # The aggregate gate compares manifests as raw bytes across the twelve
        # cells. Text mode translates "\n" to "\r\n" on Windows, so four cells
        # described the same release in different bytes and reproducibility
        # could never hold. Comparing build_manifest results cannot see it:
        # the translation happens where main() writes the file.
        module = load()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            self.fixture(root)
            output = Path(temporary) / "release.json"
            with contextlib.redirect_stdout(io.StringIO()):
                code = module.main(
                    [
                        "--root",
                        str(root),
                        "--tag",
                        "v1.6.0",
                        "--archive",
                        str(Path(temporary) / "release.tar"),
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(code, 0)
            raw = output.read_bytes()
        self.assertNotIn(b"\r\n", raw)
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(json.loads(raw)["tag"], "v1.6.0")

    def test_same_tag_builds_byte_identical_archive_and_manifest(self) -> None:
        module = load()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            self.fixture(root)
            first_archive = Path(temporary) / "first.tar"
            second_archive = Path(temporary) / "second.tar"
            first = module.build_manifest(root, "v1.6.0", first_archive)
            second = module.build_manifest(root, "v1.6.0", second_archive)
        self.assertEqual(
            first_archive.read_bytes()
            if first_archive.exists()
            else first["archive"]["sha256"],
            second_archive.read_bytes()
            if second_archive.exists()
            else second["archive"]["sha256"],
        )
        self.assertEqual(first, second)
        self.assertEqual(
            first["publicSurface"]["skills"],
            ["execute-durably", "solve-efficiently", "verify-delivery"],
        )
        self.assertEqual(first["publicSurface"]["hooks"], ["Stop"])
        self.assertEqual(first["ci"]["python"], ["3.11", "3.13"])
        self.assertEqual(
            first["ci"]["os"], ["macos-latest", "ubuntu-latest", "windows-latest"]
        )

    def test_tag_ci_reproduces_release_manifest_twice(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text(
            encoding="utf-8"
        )
        self.assertGreaterEqual(workflow.count("scripts/build_release_manifest.py"), 2)
        self.assertIn("compare-release-builds", workflow)
        self.assertIn("startsWith(github.ref, 'refs/tags/')", workflow)

    def test_tag_ci_preserves_complete_release_set_and_aggregates_six_receipts(
        self,
    ) -> None:
        workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text(
            encoding="utf-8"
        )
        for artifact in (
            "release-one.tar",
            "release-one.json",
            "release-one.sha256",
            "cognitive-powers-release-witness.json",
        ):
            self.assertIn(artifact, workflow)
        self.assertIn("aggregate-release-evidence", workflow)
        # The pin is asserted by shape, not by value. Copying the SHA here made
        # a routine action bump fail as a test about release manifests, and the
        # repair was to retype the same pin in a second place -- which is how a
        # pin stops pinning. That every action is bound to an immutable commit
        # is checked once, in test_plugin_contract.
        self.assertRegex(workflow, r"actions/download-artifact@[0-9a-f]{40}")
        self.assertIn("merge-multiple: false", workflow)
        self.assertIn("aggregate_release_artifacts.py", workflow)

    def test_additional_tag_at_release_commit_fails_closed(self) -> None:
        module = load()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            self.fixture(root)
            subprocess.run(["git", "tag", "extra"], cwd=root, check=True)
            with self.assertRaisesRegex(module.ManifestError, "exactly"):
                module.build_manifest(root, "v1.6.0", Path(temporary) / "release.tar")

    def test_manifest_uses_nul_delimited_tree_for_unusual_names(self) -> None:
        source = PATH.read_text(encoding="utf-8")
        self.assertIn('"ls-tree", "-r", "-z", tag', source)
        self.assertNotIn('"ls-tree", "-r", tag)', source)


if __name__ == "__main__":
    unittest.main()
