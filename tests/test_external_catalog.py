from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PLUGIN_ROOT / "scripts" / "external_catalog.py"
SPEC = importlib.util.spec_from_file_location("external_catalog", MODULE_PATH)
catalog = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(catalog)


class ExternalCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = catalog.load_catalog(
            PLUGIN_ROOT / "integrations" / "catalog.json"
        )

    def test_current_catalog_is_complete_and_valid(self) -> None:
        self.assertEqual(catalog.validate_catalog(self.payload), [])
        self.assertEqual(len(self.payload["sources"]), 19)

    def test_labels_resolve_to_immutable_commits(self) -> None:
        source = catalog.resolve_label(self.payload, "semantic:graphify")
        self.assertEqual(source["name"], "Graphify-Labs/graphify")
        self.assertRegex(source["commit"], r"^[0-9a-f]{40}$")

    def test_missing_license_cannot_be_approved(self) -> None:
        # Read the compare-and-swap version from the catalog instead of pinning
        # it. What this test asserts is that an unlicensed source cannot reach
        # approved; a literal made every unrelated catalog addition fail here
        # with "catalog changed since it was read", which names neither the rule
        # under test nor the edit that broke it.
        current = self.payload["meta_version"]
        updated = catalog.transition_source(
            self.payload,
            "composio-community/awesome-codex-skills",
            "candidate",
            expected_meta_version=current,
        )
        with self.assertRaisesRegex(catalog.CatalogError, "without a detected license"):
            catalog.transition_source(
                updated,
                "composio-community/awesome-codex-skills",
                "approved",
                expected_meta_version=current + 1,
            )

    def test_compare_and_swap_rejects_stale_update(self) -> None:
        with self.assertRaisesRegex(catalog.CatalogError, "changed since"):
            catalog.transition_source(
                self.payload,
                "ruvnet/ruflo",
                "candidate",
                expected_meta_version=99,
            )

    def test_label_cannot_point_to_a_moving_or_unknown_revision(self) -> None:
        broken = copy.deepcopy(self.payload)
        broken["labels"]["semantic:graphify"] = "Graphify-Labs/graphify@main"
        self.assertTrue(
            any(
                "unknown identity" in error
                for error in catalog.validate_catalog(broken)
            )
        )


if __name__ == "__main__":
    unittest.main()
