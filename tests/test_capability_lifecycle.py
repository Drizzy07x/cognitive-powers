from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "audit-capabilities" / "scripts" / "capability_lifecycle.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "test_capability_lifecycle_module", SCRIPT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lifecycle = load_module()
SOURCE = "sha256:" + "1" * 64
IMPLEMENTATION = "sha256:" + "2" * 64
PREVIOUS = "sha256:" + "3" * 64


def packet(current, requested):
    return {
        "schema_version": 1,
        "capability_id": "research-systematically",
        "current_state": current,
        "requested_state": requested,
        "events": [
            {
                "event_id": "event-one",
                "observed_at": "2026-06-01",
                "source": "rollout-one",
            },
            {
                "event_id": "event-two",
                "observed_at": "2026-07-01",
                "source": "rollout-two",
            },
        ],
        "fingerprints": {"source": SOURCE, "implementation": IMPLEMENTATION},
        "checks": [
            {
                "name": "unit-tests",
                "passed": True,
                "fingerprint": IMPLEMENTATION,
                "evidence": ["tests/test_research_protocol.py passed"],
            }
        ],
        "evidence": ["two distinct workflows required the protocol"],
        "approval": {
            "approved_by": "owner",
            "approved_at": "2026-07-20T12:00:00Z",
            "evidence": "review receipt 42",
        },
        "rollback": {
            "target_fingerprint": PREVIOUS,
            "steps": ["restore the prior manifest"],
            "executed": False,
            "evidence": [],
        },
    }


class CapabilityLifecycleTests(unittest.TestCase):
    def test_full_lifecycle_advances_one_evidence_bound_state_at_a_time(self):
        current = None
        for state in lifecycle.STATES:
            value = packet(current, state)
            if state == "retired":
                value["rollback"]["executed"] = True
                value["rollback"]["evidence"] = ["rollback check passed"]
            result = lifecycle.transition(value)
            self.assertEqual(result["to_state"], state)
            self.assertTrue(result["transition_approved"])
            self.assertRegex(result["receipt_fingerprint"], r"^sha256:[0-9a-f]{64}$")
            current = state

    def test_candidate_requires_two_distinct_events_not_duplicate_representations(self):
        value = packet("observed", "candidate")
        value["events"][1]["event_id"] = "event-one"

        with self.assertRaisesRegex(lifecycle.LifecycleError, "two distinct"):
            lifecycle.transition(value)

    def test_trial_rejects_failed_or_wrong_fingerprint_checks(self):
        failed = packet("candidate", "trial")
        failed["checks"][0]["passed"] = False
        with self.assertRaisesRegex(lifecycle.LifecycleError, "all checks"):
            lifecycle.transition(failed)

        stale = packet("candidate", "trial")
        stale["checks"][0]["fingerprint"] = PREVIOUS
        with self.assertRaisesRegex(
            lifecycle.LifecycleError, "different implementation"
        ):
            lifecycle.transition(stale)

    def test_active_requires_explicit_approval_and_rollback_plan(self):
        value = packet("trial", "active")
        value["approval"] = None
        with self.assertRaisesRegex(lifecycle.LifecycleError, "approval"):
            lifecycle.transition(value)

        value = packet("trial", "active")
        value["rollback"]["steps"] = []
        with self.assertRaisesRegex(lifecycle.LifecycleError, "rollback.steps"):
            lifecycle.transition(value)

    def test_retirement_requires_executed_evidenced_rollback(self):
        value = packet("active", "retired")
        with self.assertRaisesRegex(lifecycle.LifecycleError, "executed rollback"):
            lifecycle.transition(value)

    def test_skipping_a_state_is_rejected(self):
        with self.assertRaisesRegex(lifecycle.LifecycleError, "exactly one state"):
            lifecycle.transition(packet("observed", "trial"))


if __name__ == "__main__":
    unittest.main()
