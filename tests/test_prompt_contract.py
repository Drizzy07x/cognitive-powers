from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT / "skills" / "engineer-prompts" / "scripts" / "prompt_contract.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "test_prompt_contract_module", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


contract = load_module()


def valid_contract() -> dict[str, object]:
    return {
        "outcome": "A validated release artifact exists",
        "success_criteria": ["The focused test suite exits with code 0"],
        "boundaries": ["Only files in the assigned package may change"],
        "permissions": ["Read and edit files in the assigned package"],
        "tools": ["Local test runner"],
        "evidence": ["Exact test command and result"],
        "stop_conditions": ["Stop when all criteria pass or authority is required"],
    }


class PromptContractTests(unittest.TestCase):
    def test_valid_contract_is_normalized_without_adding_a_model(self) -> None:
        source = valid_contract()
        source["outcome"] = "  A validated release artifact exists  "
        result = contract.validate_contract(source)
        self.assertEqual(result["outcome"], "A validated release artifact exists")
        self.assertNotIn("target_model", result)

    def test_optional_target_model_is_preserved_without_a_default(self) -> None:
        source = valid_contract()
        source["target_model"] = "provider/model-version"
        result = contract.validate_contract(source)
        self.assertEqual(result["target_model"], "provider/model-version")

    def test_missing_evidence_is_rejected(self) -> None:
        source = valid_contract()
        del source["evidence"]
        with self.assertRaisesRegex(
            contract.ContractError, "missing required fields: evidence"
        ):
            contract.validate_contract(source)

    def test_empty_or_duplicate_contract_items_are_rejected(self) -> None:
        source = valid_contract()
        source["success_criteria"] = ["Tests pass", "tests pass"]
        with self.assertRaisesRegex(
            contract.ContractError, "must not contain duplicates"
        ):
            contract.validate_contract(source)

    def test_unknown_fields_are_rejected_instead_of_becoming_hidden_requirements(
        self,
    ) -> None:
        source = valid_contract()
        source["preferred_vendor"] = "example"
        with self.assertRaisesRegex(
            contract.ContractError, "unknown fields: preferred_vendor"
        ):
            contract.validate_contract(source)

    def test_render_order_is_stable_and_includes_stop_conditions(self) -> None:
        rendered = contract.render_prompt(valid_contract())
        self.assertLess(
            rendered.index("## Outcome"), rendered.index("## Success criteria")
        )
        self.assertLess(
            rendered.index("## Required evidence"), rendered.index("## Stop conditions")
        )
        self.assertIn(
            "- Stop when all criteria pass or authority is required", rendered
        )

    def test_cli_render_and_invalid_contract_exit_codes_are_real(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "contract.json"
            source.write_text(json.dumps(valid_contract()), encoding="utf-8")
            rendered = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "render", str(source)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            self.assertIn("## Required evidence", rendered.stdout)

            source.write_text("{}", encoding="utf-8")
            rejected = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "validate", str(source)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertIn(
                "missing required fields", json.loads(rejected.stdout)["error"]
            )


if __name__ == "__main__":
    unittest.main()
