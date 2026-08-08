"""Bind every release gate to the one declared version.

A version literal inside a release gate is the one drift a green suite cannot
see: the fixtures are written to agree with the constant, so the gate keeps
passing while it silently refuses -- or misnames -- the next release. These
tests fail on the literal itself rather than waiting for a tag to disagree.
"""

from __future__ import annotations

import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

from tests.test_plugin_contract import VERSION_SPELLING

ROOT = Path(__file__).resolve().parents[1]

# Gates whose subject is the release under test. Their identity must be derived.
GUARDED = (
    "scripts/aggregate_release_artifacts.py",
    "scripts/build_compatibility_matrix.py",
    "scripts/create_compatibility_receipt.py",
    "scripts/run_compatibility_scenarios.py",
    ".github/workflows/validate.yml",
    ".github/workflows/publish-release.yml",
    ".github/workflows/verify-release.yml",
)

# scripts/run_real_upgrade_rollback.ps1 is deliberately excluded: its v1.5.2 is
# the release an upgrade starts *from*, a scenario constant the compatibility
# contract names as "upgrade-v1.5.2". That is a release-process decision, not a
# stale copy of the candidate identity.

# A product tag, but not the "-v" inside a scenario identifier like
# "upgrade-v1.5.2", which names an origin release on purpose.
PRODUCT_TAG = re.compile(rf"(?<![A-Za-z0-9-])v{VERSION_SPELLING}")
ARCHIVE_LITERAL = re.compile(rf"cognitive-powers-{VERSION_SPELLING}")


def load(relative: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def changelog_version() -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(
        rf"^## ({VERSION_SPELLING}) - \d{{4}}-\d{{2}}-\d{{2}}\s*$", text, re.MULTILINE
    )
    assert match is not None, "CHANGELOG.md has no dated release heading"
    return match.group(1)


class ReleaseVersionBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = load("scripts/release_identity.py", "release_identity_binding")

    def test_declared_identity_agrees_across_every_manifest(self) -> None:
        version = changelog_version()
        self.assertEqual(self.identity.plugin_version(), version)
        self.assertEqual(self.identity.release_tag(), f"v{version}")
        for relative in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
            manifest = json.loads((ROOT / relative).read_text(encoding="utf-8"))
            with self.subTest(manifest=relative):
                self.assertEqual(manifest["version"].split("+", 1)[0], version)
        marketplace = json.loads(
            (ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [entry["version"].split("+", 1)[0] for entry in marketplace["plugins"]],
            [version],
        )

    def test_release_notes_come_from_the_changelog_section(self) -> None:
        version = changelog_version()
        notes = self.identity.release_notes(version)
        self.assertTrue(notes.strip(), "the published notes would be empty")
        self.assertTrue(notes.endswith("\n"))
        # The section must stop at the next release, or the notes would carry
        # every earlier release with them.
        self.assertNotIn("\n## ", notes)
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(notes.strip(), changelog)

    def test_release_notes_refuse_a_version_the_changelog_does_not_describe(
        self,
    ) -> None:
        for version in ("99.99.99", "not-a-version"):
            with self.subTest(version=version):
                with self.assertRaises(self.identity.ReleaseIdentityError):
                    self.identity.release_notes(version)

    def test_release_notes_are_written_with_pinned_newlines(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "notes.md"
            code = self.identity.main(["--output", str(output)])
            self.assertEqual(code, 0)
            written = output.read_bytes()
        self.assertNotIn(b"\r\n", written)
        self.assertEqual(
            written.decode("utf-8"),
            self.identity.release_notes(changelog_version()),
        )

    def test_publication_publishes_the_changelog_rather_than_commit_subjects(
        self,
    ) -> None:
        publication = (ROOT / ".github/workflows/publish-release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("--notes-file", publication)
        self.assertNotIn("--generate-notes", publication)
        self.assertIn("scripts/release_identity.py", publication)
        self.assertLess(
            publication.index("scripts/release_identity.py"),
            publication.index("gh release create"),
        )

    def test_release_gates_carry_no_hardcoded_release_identity(self) -> None:
        for relative in GUARDED:
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertEqual(
                    PRODUCT_TAG.findall(text),
                    [],
                    f"{relative} pins a release tag; derive it from "
                    "scripts/release_identity.py instead",
                )
                self.assertEqual(
                    ARCHIVE_LITERAL.findall(text),
                    [],
                    f"{relative} names a release archive literally; derive it "
                    "from the manifest that was verified",
                )

    def test_guard_detects_a_reintroduced_literal(self) -> None:
        """The guard above is only worth having if a pin actually trips it."""
        self.assertEqual(PRODUCT_TAG.findall('test "$TAG" = "v1.6.0"'), ["v1.6.0"])
        self.assertEqual(
            ARCHIVE_LITERAL.findall("output / 'cognitive-powers-1.6.0.tar'"),
            ["cognitive-powers-1.6.0"],
        )
        # ...and only if it still tolerates the origin-release scenario name.
        self.assertEqual(PRODUCT_TAG.findall('"upgrade-v1.5.2"'), [])

    def test_scenario_evidence_refuses_a_foreign_candidate_tag(self) -> None:
        module = load("scripts/run_compatibility_scenarios.py", "scenarios_binding")
        with tempfile.TemporaryDirectory() as raw:
            evidence = Path(raw) / "evidence.json"
            evidence.write_text("{}", encoding="utf-8")
            for tag in ("v0.0.1", "v1.6.0", self.identity.release_tag() + "-rc1"):
                with self.subTest(tag=tag):
                    if tag == self.identity.release_tag():
                        continue
                    with self.assertRaises(module.EvidenceError):
                        module.build_evidence(evidence, commit="a" * 40, tag=tag)

    def test_compatibility_receipt_refuses_a_stale_installation_tag(self) -> None:
        module = load("scripts/create_compatibility_receipt.py", "receipt_binding")
        commit = "a" * 40
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            validation = root / "validation.json"
            installation = root / "installation.json"
            validation.write_text(
                json.dumps(
                    {
                        "kind": "cognitive-powers-validation",
                        "passed": True,
                        "git": {"sha": commit, "dirty": False, "identityStable": True},
                    }
                ),
                encoding="utf-8",
            )
            installation.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "product": "cognitive-powers",
                        "commit": commit,
                        "tag": "v0.0.1",
                        "matched": True,
                        "readOnly": True,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(module.ReceiptError):
                module.create_receipt(
                    validation,
                    installation,
                    os_name="ubuntu-latest",
                    python="3.13",
                    codex_cli_version="0.145.0",
                    scenario="clean-install",
                    commit=commit,
                    workflow="validate.yml",
                    run_id="1",
                    run_attempt=1,
                )


if __name__ == "__main__":
    unittest.main()
