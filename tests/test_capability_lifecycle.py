from __future__ import annotations

import hashlib
import importlib.util
import json
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


def packet(current, requested, previous_receipt=None):
    value = {
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
    if previous_receipt is not None:
        value["previous_receipt"] = previous_receipt
    return value


def receipt_for(state):
    previous = None
    current = None
    for requested in lifecycle.STATES[: lifecycle.STATES.index(state) + 1]:
        value = packet(current, requested, previous)
        if requested == "retired":
            value["rollback"]["executed"] = True
            value["rollback"]["evidence"] = ["rollback check passed"]
        previous = lifecycle.transition(value)
        current = requested
    return previous


def chained_packet(current, requested):
    previous = receipt_for(current) if current is not None else None
    return packet(current, requested, previous)


class CapabilityLifecycleTests(unittest.TestCase):
    def test_full_lifecycle_advances_one_evidence_bound_state_at_a_time(self):
        current = None
        previous = None
        for state in lifecycle.STATES:
            value = packet(current, state, previous)
            if state == "retired":
                value["rollback"]["executed"] = True
                value["rollback"]["evidence"] = ["rollback check passed"]
            result = lifecycle.transition(value)
            self.assertEqual(result["to_state"], state)
            self.assertTrue(result["transition_approved"])
            self.assertRegex(result["receipt_fingerprint"], r"^sha256:[0-9a-f]{64}$")
            self.assertEqual(
                result["previous_receipt_fingerprint"],
                previous["receipt_fingerprint"] if previous is not None else None,
            )
            current = state
            previous = result

    def test_candidate_requires_two_distinct_events_not_duplicate_representations(self):
        value = chained_packet("observed", "candidate")
        value["events"][1]["event_id"] = "event-one"

        with self.assertRaisesRegex(lifecycle.LifecycleError, "two distinct"):
            lifecycle.transition(value)

    def test_trial_rejects_failed_or_wrong_fingerprint_checks(self):
        failed = chained_packet("candidate", "trial")
        failed["checks"][0]["passed"] = False
        with self.assertRaisesRegex(lifecycle.LifecycleError, "all checks"):
            lifecycle.transition(failed)

        stale = chained_packet("candidate", "trial")
        stale["checks"][0]["fingerprint"] = PREVIOUS
        with self.assertRaisesRegex(
            lifecycle.LifecycleError, "different implementation"
        ):
            lifecycle.transition(stale)

    def test_active_requires_explicit_approval_and_rollback_plan(self):
        value = chained_packet("trial", "active")
        value["approval"] = None
        with self.assertRaisesRegex(lifecycle.LifecycleError, "approval"):
            lifecycle.transition(value)

        value = chained_packet("trial", "active")
        value["rollback"]["steps"] = []
        with self.assertRaisesRegex(lifecycle.LifecycleError, "rollback.steps"):
            lifecycle.transition(value)

    def test_retirement_requires_executed_evidenced_rollback(self):
        value = chained_packet("active", "retired")
        with self.assertRaisesRegex(lifecycle.LifecycleError, "executed rollback"):
            lifecycle.transition(value)

    def test_skipping_a_state_is_rejected(self):
        with self.assertRaisesRegex(lifecycle.LifecycleError, "exactly one state"):
            lifecycle.transition(chained_packet("observed", "trial"))

    def test_transition_from_existing_state_requires_previous_receipt(self):
        with self.assertRaisesRegex(lifecycle.LifecycleError, "previous_receipt"):
            lifecycle.transition(packet("observed", "candidate"))

    def test_tampered_previous_receipt_is_rejected(self):
        value = chained_packet("candidate", "trial")
        value["previous_receipt"]["evidence"] = ["tampered"]

        with self.assertRaisesRegex(lifecycle.LifecycleError, "fingerprint mismatch"):
            lifecycle.transition(value)

    def test_previous_receipt_must_match_capability_and_state(self):
        wrong_capability = chained_packet("observed", "candidate")
        wrong_capability["previous_receipt"]["capability_id"] = "other"
        with self.assertRaisesRegex(lifecycle.LifecycleError, "capability_id"):
            lifecycle.transition(wrong_capability)

        wrong_state = chained_packet("observed", "candidate")
        wrong_state["previous_receipt"]["to_state"] = "candidate"
        with self.assertRaisesRegex(lifecycle.LifecycleError, "current_state"):
            lifecycle.transition(wrong_state)

    def test_legacy_observed_receipt_can_seed_one_chained_transition(self):
        previous = receipt_for("observed")
        previous.pop("previous_receipt_fingerprint")
        core = {
            field: previous.get(field)
            for field in lifecycle.RECEIPT_CORE_FIELDS
            if field != "previous_receipt_fingerprint"
        }
        previous["receipt_fingerprint"] = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        )

        result = lifecycle.transition(packet("observed", "candidate", previous))

        self.assertEqual(result["to_state"], "candidate")
        self.assertEqual(
            result["previous_receipt_fingerprint"], previous["receipt_fingerprint"]
        )

    def test_legacy_receipt_after_observed_cannot_bypass_chain(self):
        previous = receipt_for("candidate")
        previous.pop("previous_receipt_fingerprint")
        core = {
            field: previous.get(field)
            for field in lifecycle.RECEIPT_CORE_FIELDS
            if field != "previous_receipt_fingerprint"
        }
        previous["receipt_fingerprint"] = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        )

        with self.assertRaisesRegex(lifecycle.LifecycleError, "legacy unchained"):
            lifecycle.transition(packet("candidate", "trial", previous))


if __name__ == "__main__":
    unittest.main()
