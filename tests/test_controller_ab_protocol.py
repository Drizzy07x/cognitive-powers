import json
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = PLUGIN_ROOT / "benchmarks" / "controller_ab_protocol.json"


class ControllerAbProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))

    def test_design_cardinality_is_consistent(self) -> None:
        self.assertEqual(self.protocol["schema_version"], 3)
        self.assertEqual(
            self.protocol["protocol_id"], "cognitive-powers-controller-ab-v5"
        )
        design = self.protocol["design"]
        self.assertEqual(len(design["modes"]), 4)
        self.assertEqual(len(design["categories"]), 5)
        self.assertEqual(design["cells"], 4 * 5)
        self.assertEqual(design["repetitions_per_fixture_per_arm"], 3)
        self.assertEqual(design["arms_per_fixture"], 2)

        pilot = design["rounds"]["pilot"]
        promotion = design["rounds"]["promotion"]
        for round_contract in (pilot, promotion):
            expected_fixtures = design["cells"] * round_contract["fixtures_per_cell"]
            expected_sessions = (
                expected_fixtures
                * design["repetitions_per_fixture_per_arm"]
                * design["arms_per_fixture"]
            )
            self.assertEqual(
                round_contract["declared_fixture_count"], expected_fixtures
            )
            self.assertEqual(
                round_contract["declared_session_count"], expected_sessions
            )

        self.assertEqual(pilot["declared_fixture_count"], 20)
        self.assertEqual(pilot["declared_session_count"], 120)
        self.assertEqual(promotion["declared_fixture_count"], 60)
        self.assertEqual(promotion["declared_session_count"], 360)
        self.assertTrue(promotion["must_be_new_relative_to_pilot"])
        self.assertEqual(design["declared_total_fixture_count"], 80)
        self.assertEqual(design["declared_total_session_count"], 480)

    def test_modes_categories_and_arms_are_frozen(self) -> None:
        design = self.protocol["design"]
        self.assertEqual(
            set(design["modes"]),
            {"solo", "parallel-read-only", "parallel-packets", "staged-verify"},
        )
        self.assertEqual(
            set(design["categories"]),
            {
                "bug-fix",
                "multi-file-implementation",
                "current-source-research",
                "delivery-verification",
                "real-host-interaction",
            },
        )
        comparison = self.protocol["comparison"]
        self.assertEqual(comparison["control_arm"]["controller_mode"], "forced-solo")
        self.assertEqual(comparison["candidate_arm"]["controller_mode"], "adaptive")
        self.assertEqual(comparison["only_intended_difference"], "controller_mode")
        self.assertEqual(comparison["codex_base_role"], "exploratory-only")

    def test_promotion_gates_match_the_confirmatory_claim(self) -> None:
        gates = {gate["id"]: gate for gate in self.protocol["promotion_gates"]}
        expected = {
            "candidate-critical-failures": ("eq", 0),
            "strict-success-observed": ("gte", 0.0),
            "strict-success-noninferiority": ("gt", -0.05),
            "quality-delta": ("gte", 5.0),
            "quality-confidence": ("gt", 0.0),
            "total-token-ratio": ("lte", 0.85),
            "total-token-confidence": ("lt", 0.85),
            "fresh-input-ratio": ("lte", 0.8),
            "fresh-input-confidence": ("lt", 0.8),
            "solo-token-overhead": ("lte", 1.05),
            "mode-precision": ("gte", 0.9),
            "delegation-recall": ("gte", 0.8),
            "safety-contract-compliance": ("eq", 1.0),
            "failed-pairs-excluded-from-token-analysis": ("eq", 0),
        }
        self.assertEqual(set(gates), set(expected))
        for gate_id, (operator, threshold) in expected.items():
            self.assertEqual(gates[gate_id]["operator"], operator)
            self.assertEqual(gates[gate_id]["threshold"], threshold)

        analysis = self.protocol["analysis"]
        self.assertEqual(analysis["primary"], "intention-to-treat")
        self.assertEqual(analysis["token_comparisons"], "paired-successful-runs-only")
        self.assertEqual(analysis["confidence_intervals"]["level"], 0.95)
        self.assertEqual(analysis["confidence_intervals"]["resamples"], 10000)

    def test_identity_telemetry_and_artifacts_fail_closed(self) -> None:
        requirements = self.protocol["fail_closed_requirements"]
        self.assertEqual(requirements["identity"]["missing_or_mismatched"], "invalid")
        self.assertIn("git_identity", requirements["identity"]["required"])
        self.assertIn("fixture_sha256", requirements["identity"]["required"])
        self.assertEqual(
            requirements["telemetry"]["missing_or_inconsistent"], "invalid"
        )
        self.assertTrue(requirements["telemetry"]["descendant_tokens_included"])
        self.assertIn("agent_plan_input", requirements["telemetry"]["required"])
        self.assertIn("observed_agents", requirements["telemetry"]["required"])
        self.assertIn(
            "aggregate_fresh_input_tokens", requirements["telemetry"]["required"]
        )
        self.assertTrue(
            requirements["evaluation_isolation"]["diff_captured_before_evaluators"]
        )

        artifacts = set(self.protocol["required_artifacts"])
        self.assertEqual(
            artifacts,
            {
                "frozen-manifest.json",
                "randomized-schedule.json",
                "session-receipts.jsonl",
                "agent-events.jsonl",
                "pre-evaluator-diffs/",
                "hidden-check-results.jsonl",
                "quality-check-results.jsonl",
                "analysis-with-ci95.json",
                "sha256-index.json",
                "independent-verdict.json",
            },
        )
        self.assertTrue(self.protocol["verdict"]["all_gates_required_for_proven"])

    def test_manifest_does_not_claim_fixtures_runs_or_results_exist(self) -> None:
        self.assertEqual(self.protocol["status"], "planned")
        self.assertEqual(self.protocol["claim_status"], "not-proven")
        self.assertFalse(self.protocol["contains_fixture_definitions"])
        self.assertFalse(self.protocol["contains_execution_results"])
        self.assertFalse(self.protocol["contains_provider_evidence"])
        self.assertEqual(self.protocol["verdict"]["current"], None)
        previous = self.protocol["previous_protocol_evidence"]
        self.assertEqual(
            [item["verdict"] for item in previous],
            ["invalid", "invalid", "invalid", "invalid"],
        )
        self.assertTrue(all(not item["reusable_for_v5_claims"] for item in previous))

        state = self.protocol["execution_state"]
        self.assertEqual(state["fixtures_created"], 0)
        self.assertEqual(state["fixture_status"], "pending-v3-materialization")
        self.assertIsNone(state["fixture_lock_sha256"])
        self.assertEqual(state["sessions_completed"], 0)
        self.assertFalse(state["results_available"])
        self.assertFalse(state["provider_evidence_available"])
        for round_contract in self.protocol["design"]["rounds"].values():
            self.assertEqual(round_contract["fixture_status"], "ready")


if __name__ == "__main__":
    unittest.main()
