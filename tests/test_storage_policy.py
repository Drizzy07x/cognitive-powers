from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import storage_policy  # noqa: E402


class SourceIdentityNormalizationTests(unittest.TestCase):
    """Source identity must describe a commit, not one checkout of it."""

    def build(self, root: Path, newline: bytes) -> None:
        (root / "pkg").mkdir()
        (root / "pkg" / "module.py").write_bytes(
            newline.join([b"import os", b"", b"value = 1", b""])
        )
        (root / "README.md").write_bytes(newline.join([b"# Title", b"", b"Body", b""]))
        # A PNG header carries a NUL byte, so it must be hashed exactly.
        (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x01\x02\r\n\x03")

    def test_crlf_and_lf_checkouts_share_one_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            windows = base / "windows"
            posix = base / "posix"
            windows.mkdir()
            posix.mkdir()
            self.build(windows, b"\r\n")
            self.build(posix, b"\n")

            windows_identity = storage_policy.source_identity(windows)
            posix_identity = storage_policy.source_identity(posix)

        self.assertNotEqual(
            (windows / "pkg" / "module.py"),
            (posix / "pkg" / "module.py"),
            "the fixtures must differ on disk for this test to mean anything",
        )
        self.assertEqual(windows_identity["sha256"], posix_identity["sha256"])
        self.assertEqual(windows_identity["fileCount"], posix_identity["fileCount"])

    def test_identity_reports_its_algorithm(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.build(root, b"\n")
            identity = storage_policy.source_identity(root)
        self.assertEqual(
            identity["algorithm"], storage_policy.SOURCE_IDENTITY_ALGORITHM
        )

    def test_real_content_changes_still_change_the_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.build(root, b"\n")
            before = storage_policy.source_identity(root)
            (root / "pkg" / "module.py").write_bytes(b"import os\n\nvalue = 2\n")
            after = storage_policy.source_identity(root)
        self.assertNotEqual(before["sha256"], after["sha256"])

    def test_binary_content_is_hashed_exactly(self) -> None:
        payload = b"\x00\r\n\x01"
        self.assertEqual(storage_policy.identity_bytes(payload), payload)

    def test_text_content_folds_crlf_only(self) -> None:
        self.assertEqual(storage_policy.identity_bytes(b"a\r\nb\n"), b"a\nb\n")
        # A lone CR is content, not a line ending Git would translate.
        self.assertEqual(storage_policy.identity_bytes(b"a\rb"), b"a\rb")

    def test_binary_files_differing_only_in_line_endings_stay_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            first = base / "first"
            second = base / "second"
            first.mkdir()
            second.mkdir()
            (first / "blob.dat").write_bytes(b"\x00A\r\nB")
            (second / "blob.dat").write_bytes(b"\x00A\nB")
            self.assertNotEqual(
                storage_policy.source_identity(first)["sha256"],
                storage_policy.source_identity(second)["sha256"],
            )


class CheckoutNormalizationTests(unittest.TestCase):
    def test_repository_checks_out_lf_and_protects_binary_content(self) -> None:
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("* text=auto eol=lf", attributes)
        for suffix in (".png", ".ico", ".jpg", ".gif", ".pdf", ".zip"):
            with self.subTest(suffix=suffix):
                self.assertIn(f"*{suffix} binary", attributes)

    def test_tracked_binary_assets_are_declared_binary(self) -> None:
        completed = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            self.skipTest("not a Git checkout")
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        declared = {
            line.split(" ", 1)[0].removeprefix("*")
            for line in attributes.splitlines()
            if line.endswith(" binary")
        }
        for relative in completed.stdout.splitlines():
            path = ROOT / relative
            if not path.is_file():
                continue
            if b"\x00" not in path.read_bytes()[:8192]:
                continue
            with self.subTest(path=relative):
                self.assertIn(
                    Path(relative).suffix,
                    declared,
                    f"{relative} is binary but Git may still translate it",
                )


class StoragePolicyTests(unittest.TestCase):
    def test_shared_policy_excludes_dependency_and_generated_trees(self) -> None:
        excluded = (
            ".venv",
            "venv",
            "node_modules",
            ".next",
            "build",
            "dist",
            "coverage",
            "target",
            "vendor",
            "benchmark-results",
            "homes",
            "runs",
            "storage",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src" / "kept.py").write_text("kept\n", encoding="utf-8")
            for name in excluded:
                tree = root / name
                tree.mkdir()
                (tree / "ignored.txt").write_text(name, encoding="utf-8")

            files = [
                path.relative_to(root).as_posix()
                for path in storage_policy.iter_tree_files(root)
            ]

        self.assertEqual(files, ["src/kept.py"])
        self.assertTrue(set(excluded).issubset(storage_policy.EXCLUDED_DIRECTORY_NAMES))

    def test_tree_enumeration_and_fingerprint_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "z").mkdir()
            (root / "a").mkdir()
            (root / "z" / "second.txt").write_text("second", encoding="utf-8")
            (root / "a" / "first.txt").write_text("first", encoding="utf-8")
            first_files = list(storage_policy.iter_tree_files(root))
            first_identity = storage_policy.source_identity(root)
            (root / "node_modules").mkdir()
            (root / "node_modules" / "large.js").write_text(
                "ignored-change", encoding="utf-8"
            )
            second_files = list(storage_policy.iter_tree_files(root))
            second_identity = storage_policy.source_identity(root)

        self.assertEqual(first_files, second_files)
        self.assertEqual(first_identity, second_identity)
        self.assertEqual(
            [path.relative_to(root).as_posix() for path in first_files],
            ["a/first.txt", "z/second.txt"],
        )

    def test_graphify_generated_state_never_changes_identity_or_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "copy"
            source.mkdir()
            (source / "kept.py").write_text("kept", encoding="utf-8")
            before = storage_policy.source_identity(source)
            generated = source / "GrApHiFy-OuT"
            generated.mkdir()
            (generated / "graph.json").write_text("first", encoding="utf-8")
            after_create = storage_policy.source_identity(source)
            (generated / "graph.json").write_text("second", encoding="utf-8")
            after_change = storage_policy.source_identity(source)
            storage_policy.bounded_copy_tree(
                source, destination, max_files=10, max_bytes=100
            )

        self.assertEqual(before, after_create)
        self.assertEqual(before, after_change)
        self.assertFalse((destination / "GrApHiFy-OuT").exists())
        self.assertIn("graphify-out", storage_policy.EXCLUDED_DIRECTORY_NAMES)

    def test_bounded_copy_stops_before_file_or_byte_budget_is_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "one.txt").write_bytes(b"1234")
            (source / "two.txt").write_bytes(b"5678")

            for name, kwargs, message in (
                ("count", {"max_files": 1, "max_bytes": 100}, "file count"),
                ("bytes", {"max_files": 10, "max_bytes": 7}, "bytes"),
            ):
                with self.subTest(budget=name):
                    destination = root / name
                    with self.assertRaisesRegex(
                        storage_policy.StoragePolicyError, message
                    ):
                        storage_policy.bounded_copy_tree(
                            source,
                            destination,
                            **kwargs,
                        )
                    self.assertFalse(destination.exists())

    def test_manifest_copy_isolated_and_reports_preflight_measurement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            (source / "runtime").mkdir(parents=True)
            (source / "runtime" / "main.py").write_bytes(b"main\n")
            (source / "tests").mkdir()
            (source / "tests" / "secret.py").write_text("secret\n", encoding="utf-8")

            result = storage_policy.bounded_copy_tree(
                source,
                destination,
                manifest=("runtime",),
                max_files=10,
                max_bytes=100,
            )

            self.assertEqual(result.file_count, 1)
            self.assertEqual(result.total_bytes, 5)
            self.assertTrue((destination / "runtime" / "main.py").is_file())
            self.assertFalse((destination / "tests").exists())

    def test_fixture_copy_rejects_large_excluded_tree_unless_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "main.py").write_text("main\n", encoding="utf-8")
            dependencies = source / "node_modules"
            dependencies.mkdir()
            for index in range(3):
                (dependencies / f"{index}.js").write_text(
                    "dependency", encoding="utf-8"
                )

            with self.assertRaisesRegex(
                storage_policy.StoragePolicyError,
                r"excluded large tree.*node_modules.*override",
            ):
                storage_policy.bounded_copy_tree(
                    source,
                    root / "rejected",
                    fixture_mode=True,
                    large_tree_file_limit=2,
                    max_files=10,
                    max_bytes=100,
                )
            self.assertFalse((root / "rejected").exists())

            result = storage_policy.bounded_copy_tree(
                source,
                root / "allowed",
                fixture_mode=True,
                allow_large_excluded_trees=True,
                large_tree_file_limit=2,
                max_files=10,
                max_bytes=100,
            )

        self.assertEqual(result.file_count, 1)

    def test_manifest_cannot_hide_a_large_excluded_fixture_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "main.py").write_text("main\n", encoding="utf-8")
            dependencies = source / "node_modules"
            dependencies.mkdir()
            for index in range(3):
                (dependencies / f"{index}.js").write_text(
                    "dependency", encoding="utf-8"
                )

            with self.assertRaisesRegex(
                storage_policy.StoragePolicyError,
                r"excluded large tree.*node_modules.*override",
            ):
                storage_policy.bounded_copy_tree(
                    source,
                    root / "rejected",
                    manifest=("main.py",),
                    fixture_mode=True,
                    large_tree_file_limit=2,
                    max_files=10,
                    max_bytes=100,
                )

        self.assertFalse((root / "rejected").exists())

    def test_manifest_rejects_absolute_and_parent_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            for entry in ("../escape", str(root.resolve())):
                with self.subTest(entry=entry):
                    with self.assertRaisesRegex(
                        storage_policy.StoragePolicyError, "manifest"
                    ):
                        storage_policy.enumerate_manifest_files(source, (entry,))

    def test_git_tracked_strategy_does_not_copy_untracked_or_excluded_files(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "tracked.py").write_text("tracked\n", encoding="utf-8")
            (root / "untracked.py").write_text("untracked\n", encoding="utf-8")
            excluded = root / "node_modules"
            excluded.mkdir()
            (excluded / "forced.js").write_text("dependency\n", encoding="utf-8")
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "add",
                    "tracked.py",
                    "-f",
                    "node_modules/forced.js",
                ],
                check=True,
            )

            selected = [
                path.relative_to(root).as_posix()
                for path in storage_policy.git_tracked_files(root)
            ]

        self.assertEqual(selected, ["tracked.py"])


if __name__ == "__main__":
    unittest.main()
