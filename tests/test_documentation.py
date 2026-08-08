from __future__ import annotations

import re
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]

# Match the path wherever it appears, not only when a backtick closes right
# after ".py". Requiring that missed every command form -- `scripts/foo.py
# --flag` -- and with it a third live instance of this defect in eli5, while
# the CHANGELOG claimed the check resolved every script path. The lookbehind
# excludes a preceding "/" so the correct <plugin-root>/scripts/... spelling
# is not reported as the skill-relative one.
# A quoted path is example data, not an instruction: evidence-contract.md
# carries "repository_paths": ["scripts/release_check.py"] describing a script
# in the *user's* repository, which this plugin neither ships nor resolves.
SKILL_RELATIVE_SCRIPT = re.compile(
    r'(?<![A-Za-z0-9_/"-])scripts/[A-Za-z0-9_./-]+\.py(?!")'
)
PLUGIN_ROOT_PATH = re.compile(r"<plugin-root>/([A-Za-z0-9_./-]+)")
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


SKILL_TREES = ("skills", "skills-core")

# The same spelling means different things in the two places. Inside a skill,
# `scripts/<file>` is relative to that skill; at the root it is relative to the
# repository, which is why the two surfaces need separate checks rather than one
# shared rule.
REPOSITORY_PATH = re.compile(
    r'(?<![A-Za-z0-9_/."-])((?:scripts|tests|hooks|mcp|skills|skills-core'
    r"|benchmarks|ci|agents|integrations|evals)/[A-Za-z0-9_./-]+"
    r"\.(?:py|json|md|ps1|js))"
)
# CHANGELOG.md is deliberately absent: it is a historical record, and entries
# describing a removed script must keep naming it. Requiring existence there
# would make the gate demand that history be rewritten.
ROOT_DOCUMENTS = (
    "README.md",
    "CLAUDE.md",
    "THIRD_PARTY_NOTICES.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
)


def _skill_directory(document: Path) -> Path:
    """The skills/<name> directory a document belongs to.

    Guarded against a document outside both trees: Path("/").parent is
    Path("/") with an empty name, so the unguarded walk was a fixed point that
    spun forever instead of failing. Latent only because _documents() globs
    exactly these two trees today.
    """
    directory = document
    while directory.parent.name not in SKILL_TREES:
        if directory.parent == directory:
            raise ValueError(f"{document} is not inside {SKILL_TREES}")
        directory = directory.parent
    return directory


def _live_plugin_root_references(text: str) -> list[str]:
    """Placeholder references that name a real path, filter applied once.

    The sentence stating the convention spells the placeholder itself
    ("<plugin-root>/... is relative to"), and several commands take a literal
    argument slot such as <repo-root>. A path that ends a sentence carries the
    period into the capture; "<" cannot appear at all, the class excludes it,
    so only the placeholder check is live.
    """
    references = []
    for reference in PLUGIN_ROOT_PATH.findall(text):
        reference = reference.rstrip(".,;:")
        if reference and "..." not in reference:
            references.append(reference)
    return references


class SkillPathReferenceTests(unittest.TestCase):
    """Every path a skill tells the agent to run has to resolve.

    The skills state the rule themselves -- `scripts/<file>` is relative to the
    skill's own directory, `<plugin-root>/...` to the installed root -- and
    then two of them spelled a plugin-root script the skill-relative way.
    solve-efficiently named `scripts/run_skill_routing_benchmarks.py` three
    sections below its own statement of the convention, and its sibling
    reference file spelled the same script correctly, so nothing disagreed
    loudly enough to be noticed. An agent following the stated rule looks
    inside the skill and finds nothing.
    """

    def _documents(self) -> list[Path]:
        # skills-core is the reduced copy Codex installs. It is clean today and
        # is covered here so it cannot drift into the same defect unwatched.
        return sorted(
            document
            for tree in SKILL_TREES
            for document in (PLUGIN_ROOT / tree).glob("*/**/*.md")
        )

    def test_skill_relative_script_paths_exist_in_that_skill(self) -> None:
        # Every assertion lives inside the match loop, so a regex regression
        # that extracts nothing passes vacuously; the floor makes it red.
        total = 0
        for document in self._documents():
            skill = _skill_directory(document)
            for reference in SKILL_RELATIVE_SCRIPT.findall(
                document.read_text(encoding="utf-8")
            ):
                total += 1
                with self.subTest(document=document.name, reference=reference):
                    self.assertTrue(
                        (skill / reference).is_file(),
                        f"{document.relative_to(PLUGIN_ROOT)} points at "
                        f"{reference}, which is not in {skill.name}; if it lives "
                        "at the installed root, spell it <plugin-root>/",
                    )
        self.assertGreater(total, 0, "the scanner extracted no script paths")

    def test_plugin_root_paths_exist_at_the_plugin_root(self) -> None:
        total = 0
        for document in self._documents():
            for reference in _live_plugin_root_references(
                document.read_text(encoding="utf-8")
            ):
                total += 1
                with self.subTest(document=document.name, reference=reference):
                    self.assertTrue(
                        (PLUGIN_ROOT / reference).exists(),
                        f"{document.relative_to(PLUGIN_ROOT)} points at "
                        f"<plugin-root>/{reference}, which does not exist",
                    )
        self.assertGreater(total, 0, "the scanner extracted no plugin-root paths")

    def test_relative_markdown_links_resolve(self) -> None:
        total = 0
        for document in self._documents():
            for target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
                target = target.split("#")[0].strip()
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                total += 1
                with self.subTest(document=document.name, target=target):
                    self.assertTrue(
                        (document.parent / target).exists(),
                        f"{document.relative_to(PLUGIN_ROOT)} links to {target}",
                    )
        self.assertGreater(total, 0, "the scanner extracted no markdown links")


class RootDocumentReferenceTests(unittest.TestCase):
    """The root documents were the half of the surface nothing watched.

    Skill documents have been gated since the third live instance of a
    mis-spelled script path. The root ones were not, and they are where the
    operator-facing instructions live: three claims that this repository's own
    gates contradict shipped in 1.7.3 -- a skill-invocability rule stated
    backwards with a count that did not add up, an enforcement the packaging
    never had, and a corpus size less than half the real one. Those were prose
    rather than paths, so this cannot catch them; what it does catch is the
    cheaper and more common drift, a document naming a file that moved or went
    away, which is exactly what removing dead weight produces.
    """

    def _documents(self) -> list[Path]:
        return [
            PLUGIN_ROOT / name
            for name in ROOT_DOCUMENTS
            if (PLUGIN_ROOT / name).is_file()
        ] + sorted((PLUGIN_ROOT / "docs").glob("*.md"))

    def test_every_root_document_is_covered(self) -> None:
        """A gate that names its own inputs can shrink without anyone noticing."""
        covered = {document.name for document in self._documents()}
        present = {
            path.name
            for path in (
                *PLUGIN_ROOT.glob("*.md"),
                *(PLUGIN_ROOT / "docs").glob("*.md"),
            )
        }
        self.assertEqual(
            present - covered,
            {"CHANGELOG.md"},
            "a root document is neither covered here nor the changelog; add it "
            "to ROOT_DOCUMENTS or state why it is exempt",
        )

    def test_repository_relative_paths_exist(self) -> None:
        # A regex regression that extracts nothing passes vacuously; the
        # floor makes it red. No floor on the plugin-root scanner below: root
        # documents carry no live placeholder today.
        total = 0
        for document in self._documents():
            for reference in REPOSITORY_PATH.findall(
                document.read_text(encoding="utf-8")
            ):
                total += 1
                with self.subTest(document=document.name, reference=reference):
                    self.assertTrue(
                        (PLUGIN_ROOT / reference).exists(),
                        f"{document.relative_to(PLUGIN_ROOT)} names {reference}, "
                        "which is not in this repository",
                    )
        self.assertGreater(total, 0, "the scanner extracted no repository paths")

    def test_plugin_root_paths_exist(self) -> None:
        for document in self._documents():
            for reference in _live_plugin_root_references(
                document.read_text(encoding="utf-8")
            ):
                with self.subTest(document=document.name, reference=reference):
                    self.assertTrue(
                        (PLUGIN_ROOT / reference).exists(),
                        f"{document.relative_to(PLUGIN_ROOT)} points at "
                        f"<plugin-root>/{reference}, which does not exist",
                    )

    def test_relative_markdown_links_resolve(self) -> None:
        for document in self._documents():
            for target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
                target = target.split("#")[0].strip()
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                with self.subTest(document=document.name, target=target):
                    self.assertTrue(
                        (document.parent / target).exists(),
                        f"{document.relative_to(PLUGIN_ROOT)} links to {target}",
                    )


class DocumentationTests(unittest.TestCase):
    def test_readme_links_concise_skill_selection_and_operations_guidance(
        self,
    ) -> None:
        readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
        operations = (PLUGIN_ROOT / "docs" / "operations.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("## Choose a skill", readme)
        for skill in (
            "`$solve-efficiently`",
            "`$execute-durably`",
            "`$verify-delivery`",
        ):
            with self.subTest(skill=skill):
                self.assertIn(skill, readme)
        self.assertIn("[Operational guide](docs/operations.md)", readme)
        self.assertIn("state-migrate", operations)
        self.assertIn("dry-run", operations)
        self.assertIn("backup", operations)
        self.assertIn("no migration path", operations)
        self.assertIn("local usage counters", operations.lower())
        self.assertIn("abstained", operations)
        self.assertIn("prompts, commands, outputs, paths, or identifiers", operations)

    def test_new_operational_surface_is_standalone_and_hermes_independent(
        self,
    ) -> None:
        paths = (
            PLUGIN_ROOT / "docs" / "operations.md",
            PLUGIN_ROOT / "scripts" / "run_durability_benchmarks.py",
        )
        for path in paths:
            with self.subTest(path=path.name):
                content = path.read_text(encoding="utf-8").casefold()
                self.assertNotIn("cognitive-powers-hermes", content)
                self.assertNotIn("hermes repository", content)


if __name__ == "__main__":
    unittest.main()
