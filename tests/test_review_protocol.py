from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT / "skills" / "verify-delivery" / "scripts" / "review_protocol.py"
)


def load_protocol():
    spec = importlib.util.spec_from_file_location(
        "test_review_protocol_module", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


protocol = load_protocol()
SOURCE = "sha256:fixture-source"


def routing(**overrides):
    result = {
        "schema_version": 1,
        "cross_cutting": False,
        "release_critical": False,
        "delegated": False,
        "security_requested": False,
        "boundaries": [],
        "changed_modules": 1,
    }
    result.update(overrides)
    return result


def finding(finding_id: str, **overrides):
    result = {
        "finding_id": finding_id,
        "issue_key": "missing-boundary-test",
        "axis": "quality",
        "category": "coverage",
        "severity": "medium",
        "confidence": "high",
        "location": "tests/test_api.py",
        "problem": "The failure boundary is not tested",
        "evidence": ["only the happy path is asserted"],
        "follow_up": "Add a public-seam failure test",
        "source_identity": SOURCE,
    }
    result.update(overrides)
    return result


def verdict(axis: str, value: str = "confirmed", evidence=None):
    return {
        "pass": f"{axis}-pass",
        "axis": axis,
        "verdict": value,
        "evidence": evidence if evidence is not None else [f"{axis} evidence"],
        "source_identity": SOURCE,
    }


class ReviewProtocolTests(unittest.TestCase):
    def test_focused_review_does_not_force_security(self) -> None:
        result = protocol.select_angles(routing())

        self.assertEqual(
            result["passes"], [{"name": "focused", "axes": ["contract", "quality"]}]
        )
        self.assertFalse(result["security_review_selected"])
        self.assertFalse(result["fixed_reviewer_count"])

    def test_security_angle_requires_request_or_material_boundary(self) -> None:
        requested = protocol.select_angles(routing(security_requested=True))
        authorized = protocol.select_angles(routing(boundaries=["authorization"]))

        self.assertTrue(requested["security_review_selected"])
        self.assertTrue(authorized["security_review_selected"])
        self.assertIn(
            "security-boundary", [item["name"] for item in authorized["passes"]]
        )

    def test_duplicate_findings_merge_conservatively(self) -> None:
        payload = {
            "schema_version": 1,
            "source_identity": SOURCE,
            "findings": [
                finding("f1"),
                finding(
                    "f2",
                    severity="high",
                    confidence="medium",
                    location="src/api.py",
                    evidence=["error branch changed without a regression assertion"],
                ),
            ],
            "pass_verdicts": [verdict("contract"), verdict("quality")],
        }

        result = protocol.synthesize(payload)

        self.assertEqual(len(result["findings"]), 1)
        merged = result["findings"][0]
        self.assertEqual(merged["severity"], "high")
        self.assertEqual(merged["confidence"], "medium")
        self.assertEqual(merged["finding_ids"], ["f1", "f2"])
        self.assertEqual(result["contract_verdict"], "verified")
        self.assertEqual(result["quality_verdict"], "verified")

    def test_conflicting_summaries_make_axis_partial(self) -> None:
        payload = {
            "schema_version": 1,
            "source_identity": SOURCE,
            "findings": [
                finding("f1"),
                finding(
                    "f2", problem="The boundary test exists but asserts the wrong error"
                ),
            ],
            "pass_verdicts": [verdict("contract"), verdict("quality")],
        }

        result = protocol.synthesize(payload)

        self.assertTrue(result["findings"][0]["conflict"])
        self.assertEqual(result["quality_verdict"], "partially verified")

    def test_failed_contract_pass_maps_to_contradicted(self) -> None:
        result = protocol.synthesize(
            {
                "schema_version": 1,
                "source_identity": SOURCE,
                "findings": [],
                "pass_verdicts": [verdict("contract", "failed"), verdict("quality")],
            }
        )

        self.assertEqual(result["contract_verdict"], "contradicted")

    def test_mixed_source_identity_fails_closed(self) -> None:
        mixed = finding("f1", source_identity="sha256:different")

        with self.assertRaisesRegex(protocol.ReviewError, "different source identity"):
            protocol.synthesize(
                {
                    "schema_version": 1,
                    "source_identity": SOURCE,
                    "findings": [mixed],
                    "pass_verdicts": [verdict("contract"), verdict("quality")],
                }
            )


if __name__ == "__main__":
    unittest.main()
