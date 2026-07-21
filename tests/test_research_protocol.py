from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT / "skills" / "research-systematically" / "scripts" / "research_protocol.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "test_research_protocol_module", SCRIPT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


protocol = load_module()
ARTIFACT = "sha256:" + "a" * 64


def plan():
    return {
        "schema_version": 1,
        "research_id": "context-routing-1",
        "question": "Does bounded routing preserve answers with less context?",
        "hypotheses": [
            {
                "hypothesis_id": "h1",
                "statement": "Bounded routing preserves required facts",
                "prediction": "All required facts remain in the answer",
                "falsifier": "Any required fact is absent",
            }
        ],
        "methods": ["paired fixture comparison"],
        "experiments": [
            {
                "experiment_id": "e1",
                "mode": "confirmatory",
                "hypothesis_id": "h1",
                "procedure": "Run both profiles on the same fixture",
                "success_condition": "Candidate retains every required fact",
            }
        ],
        "stopping_rules": ["stop after all fixed fixtures run"],
    }


def result_packet(registration):
    return {
        "schema_version": 1,
        "preregistration": registration,
        "preregistration_hash": registration["preregistration_hash"],
        "evidence": [
            {
                "evidence_id": "ev1",
                "source": "artifacts/paired-result.json",
                "fingerprint": ARTIFACT,
                "observation": "Both variants retained all required facts",
            }
        ],
        "experiments": [
            {
                "experiment_id": "e1",
                "mode": "confirmatory",
                "status": "completed",
                "result": "prediction observed",
                "evidence_ids": ["ev1"],
                "deviations": [],
            }
        ],
        "claims": [
            {
                "claim_id": "c1",
                "claim": "The fixed fixture preserved required facts",
                "evidence_ids": ["ev1"],
                "status": "supported",
            }
        ],
        "dead_ends": [
            {
                "dead_end_id": "d1",
                "approach": "compare output length only",
                "reason": "length does not measure fact retention",
                "evidence_ids": ["ev1"],
            }
        ],
        "pivots": [
            {
                "pivot_id": "p1",
                "from_approach": "length comparison",
                "to_approach": "required-fact assertions",
                "reason": "the original measure was not discriminating",
                "evidence_ids": ["ev1"],
            }
        ],
        "verifier": {
            "identity": "independent-verifier",
            "verdict": "confirmed",
            "rationale": "The artifact directly records each required assertion",
            "evidence_ids": ["ev1"],
        },
    }


class ResearchProtocolTests(unittest.TestCase):
    def test_preregistration_hash_is_deterministic_and_bound_to_plan(self):
        first = protocol.preregister(plan())
        second = protocol.preregister(plan())
        self.assertEqual(first["preregistration_hash"], second["preregistration_hash"])

        changed = plan()
        changed["question"] = "A different question"
        self.assertNotEqual(
            first["preregistration_hash"],
            protocol.preregister(changed)["preregistration_hash"],
        )

    def test_complete_research_preserves_dead_ends_pivots_and_verdict(self):
        registration = protocol.preregister(plan())
        result = protocol.evaluate(result_packet(registration))

        self.assertTrue(result["research_complete"])
        self.assertEqual(result["dead_ends"][0]["dead_end_id"], "d1")
        self.assertEqual(result["pivots"][0]["pivot_id"], "p1")
        self.assertEqual(result["verifier"]["verdict"], "confirmed")

    def test_changed_preregistration_fails_closed(self):
        registration = protocol.preregister(plan())
        packet = result_packet(registration)
        packet["preregistration"]["question"] = "Changed after results"

        with self.assertRaisesRegex(protocol.ResearchError, "hash"):
            protocol.evaluate(packet)

    def test_unplanned_experiment_cannot_be_called_confirmatory(self):
        registration = protocol.preregister(plan())
        packet = result_packet(registration)
        packet["experiments"][0]["experiment_id"] = "surprise"

        with self.assertRaisesRegex(protocol.ResearchError, "pre-registered"):
            protocol.evaluate(packet)

        packet["experiments"][0]["mode"] = "exploratory"
        result = protocol.evaluate(packet)
        self.assertFalse(result["research_complete"])
        self.assertEqual(result["missing_confirmatory_experiments"], ["e1"])

    def test_claim_without_real_evidence_reference_is_rejected(self):
        registration = protocol.preregister(plan())
        packet = result_packet(registration)
        packet["claims"][0]["evidence_ids"] = ["missing"]

        with self.assertRaisesRegex(protocol.ResearchError, "unknown evidence"):
            protocol.evaluate(packet)

    def test_inconclusive_verifier_prevents_completion(self):
        registration = protocol.preregister(plan())
        packet = result_packet(registration)
        packet["verifier"]["verdict"] = "inconclusive"

        self.assertFalse(protocol.evaluate(packet)["research_complete"])


if __name__ == "__main__":
    unittest.main()
