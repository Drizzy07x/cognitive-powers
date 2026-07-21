from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT
    / "skills"
    / "communicate-efficiently"
    / "scripts"
    / "communication_contract.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "test_communication_contract_module", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


contract = load_module()


class CommunicationContractTests(unittest.TestCase):
    def test_routine_progress_selects_compact(self) -> None:
        result = contract.select_profile(
            {
                "kind": "progress",
                "complexity": "low",
                "consequence": "reversible",
                "unresolved": False,
                "evidence_count": 1,
            }
        )
        self.assertEqual(result["profile"], "compact")

    def test_irreversible_warning_overrides_low_complexity(self) -> None:
        result = contract.select_profile(
            {
                "kind": "warning",
                "complexity": "low",
                "consequence": "irreversible",
                "unresolved": False,
                "evidence_count": 0,
            }
        )
        self.assertEqual(result["profile"], "explicit")

    def test_assessment_rejects_short_output_that_loses_evidence(self) -> None:
        case = {
            "id": "lossy",
            "expected_profile": "compact",
            "max_words": 10,
            "required_facts": ["57 tests passed"],
            "exact_literals": ["liveProviderValidated=false"],
            "forbidden_filler": [],
        }
        result = contract.assess_output(case, "Done.")
        self.assertFalse(result["passed"])
        self.assertFalse(result["integrityPassed"])

    def test_receipts_use_provider_counts_and_compare_only_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline_source = root / "baseline-source.json"
            candidate_source = root / "candidate-source.json"
            baseline_source.write_text(
                json.dumps(
                    {
                        "provider": "fixture",
                        "model": "fixture",
                        "usage": {
                            "input_tokens": 1000,
                            "cached_input_tokens": 400,
                            "output_tokens": 500,
                        },
                    }
                ),
                encoding="utf-8",
            )
            candidate_source.write_text(
                json.dumps(
                    {
                        "provider": "fixture",
                        "model": "fixture",
                        "usage": {
                            "input_tokens": 900,
                            "cached_input_tokens": 400,
                            "output_tokens": 250,
                        },
                    }
                ),
                encoding="utf-8",
            )
            baseline = contract.create_receipt(
                baseline_source,
                task_id="task-1",
                variant="baseline",
                success=True,
                quality_score=90,
                critical_failure=False,
            )
            candidate = contract.create_receipt(
                candidate_source,
                task_id="task-1",
                variant="adaptive",
                success=True,
                quality_score=92,
                critical_failure=False,
            )
            comparison = contract.compare_receipts(baseline, candidate)
            self.assertTrue(comparison["eligibleForEfficiencyClaim"])
            self.assertEqual(candidate["usage"]["freshInputTokens"], 500)
            self.assertEqual(
                comparison["metrics"]["totalTokens"]["reductionPercent"], 23.33
            )
            candidate["success"] = False
            self.assertFalse(
                contract.compare_receipts(baseline, candidate)[
                    "eligibleForEfficiencyClaim"
                ]
            )

    def test_receipt_rejects_cached_tokens_above_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "usage.json"
            source.write_text(
                json.dumps(
                    {
                        "usage": {
                            "input_tokens": 10,
                            "cached_input_tokens": 11,
                            "output_tokens": 2,
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(contract.ContractError):
                contract.create_receipt(
                    source,
                    task_id="task",
                    variant="candidate",
                    success=True,
                    quality_score=100,
                    critical_failure=False,
                )


if __name__ == "__main__":
    unittest.main()
