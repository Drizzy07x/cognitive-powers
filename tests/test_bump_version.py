from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "bump_version.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "test_bump_version_module", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bump = load_module()


def build_tree(root: Path, *, old: str = "1.7.1", new: str = "1.7.2") -> None:
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".claude-plugin").mkdir()
    (root / "docs").mkdir()
    (root / "scripts").mkdir()
    (root / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "cognitive-powers", "version": old}), encoding="utf-8"
    )
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "cognitive-powers", "version": old}), encoding="utf-8"
    )
    (root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"plugins": [{"name": "cognitive-powers", "version": old}]}),
        encoding="utf-8",
    )
    (root / "install.ps1").write_text(
        f'[string]$ReleaseRef = "v{old}"\n', encoding="utf-8"
    )
    (root / "README.md").write_text(
        f"Install with ?ref=v{old} and resolve `v{old}` first.\n"
        f"To roll back run the installer with -ReleaseRef v1.5.2\n"
        f"The upgrade-v1.5.2 scenario keeps its own origin.\n",
        encoding="utf-8",
    )
    (root / "docs" / "operations.md").write_text(
        f"Verify with --tag v{old} and the upgrade-v1.5.2 scenario.\n",
        encoding="utf-8",
    )
    (root / "docs" / "releases.json").write_text(
        json.dumps({"published": [f"v{old}", "v1.5.2", "v1.5.1"]}), encoding="utf-8"
    )
    (root / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## {new} - 2026-07-29\n\n- Everything the release carries.\n\n"
        f"## {old} - 2026-07-28\n\n- The previous release.\n",
        encoding="utf-8",
    )
    (root / "scripts" / "run_real_upgrade_rollback.ps1").write_text(
        '& $installer -ReleaseRef "v1.5.2"\n', encoding="utf-8"
    )


class BumpVersionTests(unittest.TestCase):
    def test_bump_moves_every_carrier_and_derives_the_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_tree(root)
            code = bump.main(["1.7.2", "--root", str(root)])
            self.assertEqual(code, 0)
            for relative in (
                ".codex-plugin/plugin.json",
                ".claude-plugin/plugin.json",
            ):
                declared = json.loads((root / relative).read_text(encoding="utf-8"))
                self.assertEqual(declared["version"], "1.7.2", relative)
            marketplace = json.loads(
                (root / ".claude-plugin" / "marketplace.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(marketplace["plugins"][0]["version"], "1.7.2")
            self.assertIn(
                '[string]$ReleaseRef = "v1.7.2"',
                (root / "install.ps1").read_text(encoding="utf-8"),
            )
            readme = (root / "README.md").read_text(encoding="utf-8")
            self.assertIn("?ref=v1.7.2", readme)
            # The rollback target moves to the newest published release.
            self.assertIn("-ReleaseRef v1.7.1", readme)
            # Scenario identifiers are origins, never carriers.
            self.assertIn("upgrade-v1.5.2", readme)
            operations = (root / "docs" / "operations.md").read_text(encoding="utf-8")
            self.assertIn("--tag v1.7.2", operations)
            self.assertIn("upgrade-v1.5.2", operations)
            # The lifecycle harness keeps its origin constant untouched.
            self.assertEqual(
                (root / "scripts" / "run_real_upgrade_rollback.ps1").read_text(
                    encoding="utf-8"
                ),
                '& $installer -ReleaseRef "v1.5.2"\n',
            )
            self.assertEqual(bump.main(["--check", "--root", str(root)]), 0)

    def test_bump_refuses_to_run_ahead_of_the_changelog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_tree(root)
            code = bump.main(["1.9.9", "--root", str(root)])
        self.assertEqual(code, 2)

    def test_check_names_every_stale_carrier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_tree(root)
            # Carriers still at the old version while the changelog moved on.
            code = bump.main(["--check", "--root", str(root)])
        self.assertEqual(code, 2)

    def test_repository_carriers_are_aligned_right_now(self) -> None:
        """The real tree must always pass --check between releases."""
        code = bump.main(["--check", "--root", str(PLUGIN_ROOT)])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
