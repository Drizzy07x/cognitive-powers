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


if __name__ == "__main__":
    unittest.main()
