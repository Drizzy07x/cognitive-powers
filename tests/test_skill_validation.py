from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "validate_skills.py"


def load_validator():
    spec = importlib.util.spec_from_file_location("test_skill_validator", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = load_validator()


class SkillValidationTests(unittest.TestCase):
    def test_current_skills_satisfy_contract(self) -> None:
        self.assertEqual(validator.validate(PLUGIN_ROOT), [])

    def test_current_skills_pass_quality_gate(self) -> None:
        self.assertEqual(validator.quality_warnings(PLUGIN_ROOT), [])

    def test_invalid_skill_reports_behavioral_contract_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            skill = root / "skills" / "sample-skill"
            (skill / "agents").mkdir(parents=True)
            placeholder = "[" + "TODO: finish]"
            (skill / "SKILL.md").write_text(
                "---\n"
                "name: wrong-name\n"
                f"description: {placeholder}\n"
                "---\n\n"
                "Read [missing](references/missing.md).\n",
                encoding="utf-8",
            )
            (skill / "agents" / "openai.yaml").write_text(
                'interface:\n  short_description: "tiny"\n'
                '  default_prompt: "Run this workflow."\n',
                encoding="utf-8",
            )

            errors = validator.validate(root)

            self.assertTrue(any("must match folder" in error for error in errors))
            self.assertTrue(any("missing or unfinished" in error for error in errors))
            self.assertTrue(any("scaffold placeholder" in error for error in errors))
            self.assertTrue(any("broken local link" in error for error in errors))
            self.assertTrue(any("25-64 characters" in error for error in errors))
            self.assertTrue(
                any("must mention $wrong-name" in error for error in errors)
            )
            warnings = validator.quality_warnings(root)
            self.assertTrue(any("decidable" in warning for warning in warnings))
            self.assertTrue(any("explicit sections" in warning for warning in warnings))
            self.assertTrue(
                any("missing '## Pause points'" in error for error in errors)
            )

    def test_pause_point_contract_fails_in_both_directions(self) -> None:
        """The 1.8.0 checklist contract was the one invariant nothing held.

        Real files on disk rather than mocked reads: a mocked read would prove
        the rule against text the validator never parses.
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            skill = root / "skills" / "sample-skill"
            (skill / "agents").mkdir(parents=True)
            items = "\n".join(f"- item {index}" for index in range(11))
            (skill / "SKILL.md").write_text(
                "---\n"
                "name: sample-skill\n"
                "description: Verify a sample. Use when testing the gate.\n"
                "---\n\n"
                "## Work\n\nDo not skip verification evidence.\n\n"
                "## Pause points\n\n"
                "DO-CONFIRM: confirm each item.\n\n"
                f"**Before done**\n{items}\n",
                encoding="utf-8",
            )
            (skill / "agents" / "openai.yaml").write_text(
                'interface:\n  short_description: "Verify the sample gate rule"\n'
                '  default_prompt: "Use $sample-skill now."\n',
                encoding="utf-8",
            )
            core = root / "skills-core" / "sample-core"
            (core / "agents").mkdir(parents=True)
            (core / "SKILL.md").write_text(
                "---\n"
                "name: sample-core\n"
                "description: Route a sample. Use when testing the gate.\n"
                "---\n\n"
                "## Work\n\nDo not skip verification evidence.\n\n"
                "## Pause points\n\n"
                "DO-CONFIRM: compressed prose carries the contract here.\n",
                encoding="utf-8",
            )
            (core / "agents" / "openai.yaml").write_text(
                'interface:\n  short_description: "Route the sample gate rule"\n'
                '  default_prompt: "Use $sample-core now."\n',
                encoding="utf-8",
            )
            (root / ".codex-plugin").mkdir()
            (root / ".codex-plugin" / "plugin.json").write_text(
                '{"skills": "./skills-core/"}\n', encoding="utf-8"
            )

            errors = validator.validate(root)

            self.assertTrue(
                any("exceeds 10 items" in error for error in errors), errors
            )
            # The compressed-prose core carries heading and DO-CONFIRM only;
            # demanding bullets there would fail every shipped router.
            self.assertFalse(
                any("sample-core" in error and "pause" in error for error in errors),
                errors,
            )


if __name__ == "__main__":
    unittest.main()
