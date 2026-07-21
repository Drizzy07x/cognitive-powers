from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "verify-delivery" / "scripts" / "knowledge_closeout.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "test_knowledge_closeout_module", SCRIPT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


closeout = load_module()


def surface(required=True, status="current", evidence=None):
    return {
        "required": required,
        "status": status,
        "evidence": evidence
        if evidence is not None
        else ["current artifact inspected"],
    }


def packet(mode="full"):
    return {
        "schema_version": 1,
        "mode": mode,
        "source_identity": "sha256:delivery-fixture",
        "surfaces": {name: surface() for name in closeout.SURFACES},
        "cleanup_requests": [],
        "memory_write_requests": [],
        "cleanup_authorized": False,
        "memory_write_authorized": False,
    }


class KnowledgeCloseoutTests(unittest.TestCase):
    def test_full_mode_requires_and_reports_all_six_surfaces(self):
        result = closeout.assess(packet())
        self.assertEqual(len(result["surfaces"]), 6)
        self.assertTrue(result["closeout_ready"])
        self.assertFalse(result["writes_performed"])

    def test_full_mode_rejects_an_unassessed_surface(self):
        value = packet()
        del value["surfaces"]["release_notes"]
        with self.assertRaisesRegex(closeout.CloseoutError, "all six"):
            closeout.assess(value)

    def test_light_mode_allows_only_relevant_surfaces(self):
        value = packet("light")
        value["surfaces"] = {
            "documentation": surface(),
            "release_notes": surface(
                required=False, status="not-applicable", evidence=[]
            ),
        }
        result = closeout.assess(value)
        self.assertTrue(result["closeout_ready"])
        self.assertEqual(
            [
                item["name"]
                for item in result["surfaces"]
                if item["status"] == "not-assessed"
            ],
            ["code", "tests", "project_guidance", "durable_memory"],
        )

    def test_pending_required_surface_blocks_closeout(self):
        value = packet()
        value["surfaces"]["tests"] = surface(status="pending", evidence=[])
        result = closeout.assess(value)
        self.assertFalse(result["closeout_ready"])
        self.assertEqual(result["pending_surfaces"], ["tests"])

    def test_cleanup_and_memory_requests_are_blocked_without_authorization(self):
        value = packet()
        value["cleanup_requests"] = ["delete temporary evidence"]
        value["memory_write_requests"] = ["save a durable project decision"]
        result = closeout.assess(value)
        self.assertFalse(result["closeout_ready"])
        self.assertEqual(
            [item["kind"] for item in result["blocked_actions"]],
            ["cleanup", "memory-write"],
        )
        self.assertFalse(result["cleanup_performed"])
        self.assertFalse(result["memory_writes_performed"])

    def test_authorization_reports_actions_but_still_performs_no_writes(self):
        value = packet()
        value["cleanup_requests"] = ["remove scratch output"]
        value["memory_write_requests"] = ["record adopted protocol"]
        value["cleanup_authorized"] = True
        value["memory_write_authorized"] = True
        result = closeout.assess(value)
        self.assertTrue(result["closeout_ready"])
        self.assertEqual(len(result["authorized_actions"]), 2)
        self.assertFalse(result["writes_performed"])


if __name__ == "__main__":
    unittest.main()
