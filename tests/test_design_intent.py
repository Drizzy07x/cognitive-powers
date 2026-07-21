from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT / "skills" / "design-intentionally" / "scripts" / "design_intent.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "test_design_intent_module", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


intent_module = load_module()


class DesignIntentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.brief_path = self.root / "brief.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def normalize(self, brief: dict[str, object]) -> dict[str, object]:
        self.brief_path.write_text(json.dumps(brief), encoding="utf-8")
        return intent_module.normalize_brief(brief, self.brief_path)

    def test_existing_system_and_constraints_are_preserved(self) -> None:
        result = self.normalize(
            {
                "project_root": str(self.workspace),
                "page_kind": "marketing",
                "mode": "preserve",
                "audience": "technical buyers",
                "direction": ["premium", "restrained"],
                "avoid": ["generic feature cards"],
                "brand_constraints": ["keep cobalt accent"],
                "content_constraints": ["do not invent customers"],
                "preserve": ["routes", "legal copy"],
                "existing_system": "Fluent UI",
                "references": [
                    {
                        "kind": "url",
                        "value": "https://example.test",
                        "note": "spacing reference",
                    }
                ],
            }
        )
        self.assertTrue(result["readyToImplement"])
        self.assertEqual(result["foundationDecision"], "reuse-existing")
        self.assertEqual(result["existingSystem"], "Fluent UI")
        self.assertEqual(result["preserve"], ["routes", "legal copy"])
        self.assertEqual(result["dials"], {"variance": 7, "motion": 5, "density": 2})
        self.assertEqual(len(result["intentSha256"]), 64)

    def test_ambiguous_brief_is_not_marked_ready(self) -> None:
        result = self.normalize(
            {"project_root": str(self.workspace), "mode": "greenfield"}
        )
        self.assertFalse(result["readyToImplement"])
        self.assertEqual(
            result["unresolvedChoices"], ["page_kind", "audience", "direction"]
        )

    def test_redesign_requires_explicit_preservation_boundary(self) -> None:
        result = self.normalize(
            {
                "project_root": str(self.workspace),
                "page_kind": "product",
                "mode": "overhaul",
                "audience": "operators",
                "direction": ["technical"],
            }
        )
        self.assertFalse(result["readyToImplement"])
        self.assertIn("preserve", result["unresolvedChoices"])

    def test_invalid_dial_is_rejected(self) -> None:
        with self.assertRaises(intent_module.IntentError):
            self.normalize(
                {
                    "project_root": str(self.workspace),
                    "page_kind": "dashboard",
                    "audience": "analysts",
                    "direction": ["dense"],
                    "dials": {"motion": 11},
                }
            )

    def test_workspace_output_requires_explicit_override(self) -> None:
        output = self.workspace / "design-intent.json"
        with self.assertRaises(intent_module.IntentError):
            intent_module.validate_output_path(output, self.workspace, False)
        intent_module.validate_output_path(output, self.workspace, True)


if __name__ == "__main__":
    unittest.main()
