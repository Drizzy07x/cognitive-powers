from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT
    / "skills"
    / "diagnose-systematically"
    / "scripts"
    / "investigation_protocol.py"
)


def load_protocol():
    spec = importlib.util.spec_from_file_location(
        "test_investigation_protocol_module", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


protocol = load_protocol()


def signals(**overrides):
    result = {
        "schema_version": 1,
        "symptom_reproduced": True,
        "affected_components": 1,
        "plausible_failure_seams": 1,
        "intermittent": False,
        "recent_change_uncertain": False,
        "cheap_discriminator_available": False,
    }
    result.update(overrides)
    return result


def finding(role: str, **overrides):
    result = {
        "role": role,
        "hypothesis_key": "cache-order",
        "hypothesis": "The cache is read before configuration is applied",
        "prediction": "Disabling the cache removes the failure",
        "falsifier": "The failure remains with the cache disabled",
        "evidence": ["trace shows cache read first"],
        "missing_evidence": ["controlled cache-off run"],
        "proof_step": "Run the existing test with cache disabled",
        "confidence": "high",
    }
    result.update(overrides)
    return result


class InvestigationProtocolTests(unittest.TestCase):
    def test_unreproduced_or_cheaply_discriminated_bug_stays_focused(self) -> None:
        unreproduced = protocol.route(
            signals(
                symptom_reproduced=False,
                affected_components=4,
                plausible_failure_seams=3,
                intermittent=True,
                recent_change_uncertain=True,
            )
        )
        cheap_probe = protocol.route(
            signals(
                affected_components=3,
                plausible_failure_seams=3,
                cheap_discriminator_available=True,
            )
        )

        self.assertEqual(unreproduced["mode"], "focused")
        self.assertIn("reproduce", unreproduced["reasons"][0])
        self.assertEqual(cheap_probe["mode"], "focused")
        self.assertIn("cheap", cheap_probe["reasons"][0])

    def test_reproduced_cross_component_bug_selects_bounded_parallel_lanes(
        self,
    ) -> None:
        result = protocol.route(
            signals(
                affected_components=3,
                plausible_failure_seams=2,
                recent_change_uncertain=True,
            )
        )

        self.assertEqual(result["mode"], "parallel-read-only")
        self.assertEqual(
            result["lanes"],
            [
                "reproduction-scope",
                "code-path-failure-seam",
                "recent-change-regression",
                "proof-observability",
            ],
        )
        self.assertFalse(result["fix_authorized"])

    def test_synthesis_merges_exact_keys_and_preserves_lower_confidence(self) -> None:
        payload = {
            "schema_version": 1,
            "symptom_reproduced": True,
            "findings": [
                finding("code-path"),
                finding(
                    "regression",
                    prediction="Reverting commit abc removes the failure",
                    falsifier="Reverting commit abc leaves the failure",
                    evidence=["commit abc moved configuration after cache startup"],
                    confidence="medium",
                ),
            ],
        }

        result = protocol.synthesize(payload)

        self.assertEqual(len(result["hypotheses"]), 1)
        hypothesis = result["hypotheses"][0]
        self.assertEqual(hypothesis["confidence"], "medium")
        self.assertEqual(len(hypothesis["predictions"]), 2)
        self.assertEqual(hypothesis["roles"], ["code-path", "regression"])
        self.assertFalse(result["root_cause_proven"])

    def test_evidence_free_hypothesis_cannot_keep_high_confidence(self) -> None:
        payload = {
            "schema_version": 1,
            "symptom_reproduced": True,
            "findings": [finding("proof", evidence=[], confidence="high")],
        }

        result = protocol.synthesize(payload)

        self.assertEqual(result["leading_hypothesis"]["confidence"], "low")
        self.assertFalse(result["root_cause_proven"])

    def test_missing_falsifier_is_rejected(self) -> None:
        invalid = finding("proof")
        invalid.pop("falsifier")

        with self.assertRaisesRegex(protocol.InvestigationError, "falsifier"):
            protocol.synthesize(
                {
                    "schema_version": 1,
                    "symptom_reproduced": True,
                    "findings": [invalid],
                }
            )


if __name__ == "__main__":
    unittest.main()
