from __future__ import annotations

import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


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
