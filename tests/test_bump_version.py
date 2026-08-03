from __future__ import annotations

import importlib.util
import json
import subprocess
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
    (root / "install.sh").write_text(f'release_ref="v{old}"\n', encoding="utf-8")
    (root / "README.md").write_text(
        f"Install with ?ref=v{old} and resolve `v{old}` first.\n"
        f"To roll back run the installer with -ReleaseRef v1.5.2\n"
        f"The upgrade-v1.5.2 scenario keeps its own origin.\n",
        encoding="utf-8",
    )
    # The runbook carries an install example spelled with the same option as
    # the README's rollback sentence. Protecting both by pattern is what let
    # the 1.8.2 bump move this line backwards while its sibling moved forwards.
    (root / "docs" / "operations.md").write_text(
        f"Verify with --tag v{old} and the upgrade-v1.5.2 scenario.\n"
        f"& ./install.ps1 -ReleaseRef v{old}\n"
        f"./install.sh --release-ref v{old}\n",
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
            self.assertIn(
                'release_ref="v1.7.2"',
                (root / "install.sh").read_text(encoding="utf-8"),
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

    def test_an_install_example_is_not_moved_to_the_rollback_target(self) -> None:
        """The runbook has no rollback command, so its tags name the release.

        The regression this pins: the rollback phrase was protected by pattern
        across both documents, and `docs/operations.md`'s PowerShell install
        example is spelled with the same option. The 1.8.2 bump therefore moved
        it back three releases while the POSIX example beside it moved forward,
        directly under a sentence promising the two could not diverge.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_tree(root)
            self.assertEqual(bump.main(["1.7.2", "--root", str(root)]), 0)
            operations = (root / "docs" / "operations.md").read_text(encoding="utf-8")
            self.assertIn("-ReleaseRef v1.7.2", operations)
            self.assertIn("--release-ref v1.7.2", operations)
            self.assertNotIn("v1.7.1", operations)
            # The README keeps the rollback target, which is a different tag.
            readme = (root / "README.md").read_text(encoding="utf-8")
            self.assertIn("-ReleaseRef v1.7.1", readme)

    def test_check_fails_when_the_runbook_names_a_stale_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_tree(root)
            self.assertEqual(bump.main(["1.7.2", "--root", str(root)]), 0)
            operations = root / "docs" / "operations.md"
            operations.write_text(
                operations.read_text(encoding="utf-8").replace(
                    "-ReleaseRef v1.7.2", "-ReleaseRef v1.5.1"
                ),
                encoding="utf-8",
            )
            self.assertEqual(bump.main(["--check", "--root", str(root)]), 2)

    def test_repository_carriers_are_aligned_right_now(self) -> None:
        """The real tree must always pass --check between releases."""
        code = bump.main(["--check", "--root", str(PLUGIN_ROOT)])
        self.assertEqual(code, 0)


def _tags_in_checkout() -> set[str]:
    """Tags this checkout can see, or an empty set when it can see none."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(PLUGIN_ROOT), "tag", "--list"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if completed.returncode != 0:
        return set()
    return {line.strip() for line in completed.stdout.splitlines() if line.strip()}


class PublishedReleaseTests(unittest.TestCase):
    """What `docs/releases.json` may claim, checked against the real refs.

    Only one direction is decidable here. That every published entry names a
    tag which exists is a fact about this checkout. That every existing tag
    belongs in the file is *not*: the file's bar is "pushed and validated", and
    v1.8.0 is pushed to origin with no GitHub release behind it, so asserting
    the reverse would fail on a correct state. The omission that direction
    would catch is instead caught by the release checklist step that adds a tag
    after publication.
    """

    def setUp(self) -> None:
        self.published = bump.published_releases(PLUGIN_ROOT)
        self.tags = _tags_in_checkout()

    def test_entries_are_well_formed_and_strictly_newest_first(self) -> None:
        parsed = []
        for tag in self.published:
            self.assertRegex(tag, r"^v\d+\.\d+\.\d+$")
            parsed.append(tuple(int(part) for part in tag[1:].split(".")))
        self.assertEqual(len(set(parsed)), len(parsed), "a release is listed twice")
        self.assertEqual(parsed, sorted(parsed, reverse=True), "not newest first")

    def test_every_published_release_names_a_tag_that_exists(self) -> None:
        if not self.tags:
            self.skipTest("checkout carries no tags")
        missing = [tag for tag in self.published if tag not in self.tags]
        self.assertEqual(missing, [], "published names tags that do not exist")

    def test_the_documented_rollback_target_is_a_tag_that_exists(self) -> None:
        """A rollback command is only useful if its ref resolves.

        This is the failure `bump_version`'s own docstring cites from the
        1.6.0/1.7.0 era -- the README documenting a rollback to a tag that does
        not exist -- asserted rather than described.
        """
        if not self.tags:
            self.skipTest("checkout carries no tags")
        version = bump.changelog_version(PLUGIN_ROOT)
        rollback = bump.rollback_target(PLUGIN_ROOT, version)
        self.assertIn(rollback, self.tags)
        readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"-ReleaseRef {rollback}", readme)


if __name__ == "__main__":
    unittest.main()
