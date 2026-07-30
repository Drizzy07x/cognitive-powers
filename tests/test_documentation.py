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
        for document in self._documents():
            skill = _skill_directory(document)
            for reference in SKILL_RELATIVE_SCRIPT.findall(
                document.read_text(encoding="utf-8")
            ):
                with self.subTest(document=document.name, reference=reference):
                    self.assertTrue(
                        (skill / reference).is_file(),
                        f"{document.relative_to(PLUGIN_ROOT)} points at "
                        f"{reference}, which is not in {skill.name}; if it lives "
                        "at the installed root, spell it <plugin-root>/",
                    )

    def test_plugin_root_paths_exist_at_the_plugin_root(self) -> None:
        for document in self._documents():
            for reference in PLUGIN_ROOT_PATH.findall(
                document.read_text(encoding="utf-8")
            ):
                # The sentence stating the convention spells the placeholder
                # itself ("<plugin-root>/... is relative to"), and several
                # commands take a literal argument slot such as <repo-root>.
                # A path that ends a sentence carries the period into the
                # capture; "<" cannot appear at all, the class excludes it, so
                # only the placeholder check is live.
                reference = reference.rstrip(".,;:")
                if not reference or "..." in reference:
                    continue
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
