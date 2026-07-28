from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT / "skills" / "use-current-docs" / "scripts" / "context7_lookup.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "test_context7_lookup_module", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


context7 = load_module()


class Context7LookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.cache = self.root.parent / f"{self.root.name}-cache"
        self.environment = mock.patch.dict(
            os.environ, {"COGNITIVE_POWERS_DATA": str(self.cache)}
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary_directory.cleanup()
        if self.cache.exists():
            import shutil

            shutil.rmtree(self.cache)

    def test_naive_cache_expiry_is_coerced_not_a_typeerror(self) -> None:
        # A hand-edited or foreign cache entry can carry a naive stamp, and
        # comparing it with the aware clock raised TypeError -- outside
        # main()'s except tuple, so the lookup died with a traceback.
        parsed = context7._parse_expiry("2999-01-01T00:00:00")
        self.assertIsNotNone(parsed)
        self.assertIsNotNone(parsed.tzinfo)
        self.assertTrue(parsed > context7.utc_now())
        self.assertIsNone(context7._parse_expiry("not-a-date"))
        self.assertIsNone(context7._parse_expiry(None))

    def test_error_class_does_not_shadow_the_builtin(self) -> None:
        # Naming it LookupError silently removed KeyError and IndexError --
        # builtin LookupError subclasses -- from main()'s except tuple.
        self.assertFalse(
            hasattr(context7, "LookupError")
            and getattr(context7, "LookupError") is not LookupError
        )
        self.assertTrue(issubclass(context7.Context7LookupError, RuntimeError))
        self.assertFalse(issubclass(KeyError, context7.Context7LookupError))

    def test_detects_locked_javascript_and_dotnet_versions(self) -> None:
        (self.root / "package.json").write_text(
            json.dumps({"dependencies": {"react": "^19.0.0"}}), encoding="utf-8"
        )
        (self.root / "package-lock.json").write_text(
            json.dumps({"dependencies": {"react": {"version": "19.1.2"}}}),
            encoding="utf-8",
        )
        (self.root / "sample.csproj").write_text(
            '<Project><ItemGroup><PackageReference Include="Serilog" Version="4.3.0" /></ItemGroup></Project>',
            encoding="utf-8",
        )

        dependencies = context7.discover_dependencies(self.root)
        by_name = {item["name"]: item for item in dependencies}

        self.assertEqual(by_name["react"]["version"], "19.1.2")
        self.assertEqual(by_name["react"]["kind"], "locked")
        self.assertEqual(by_name["Serilog"]["version"], "4.3.0")

    def test_prefers_exact_version_over_unversioned_high_score(self) -> None:
        selected = context7.select_library_candidate(
            [
                {
                    "id": "/other/react",
                    "title": "React",
                    "sourceReputation": "High",
                    "benchmarkScore": 99,
                    "versions": ["v18.3.1"],
                },
                {
                    "id": "/facebook/react",
                    "title": "React",
                    "sourceReputation": "High",
                    "benchmarkScore": 80,
                    "versions": ["v19.1.2"],
                },
            ],
            "react",
            "19.1.2",
        )

        self.assertEqual(selected["id"], "/facebook/react/v19.1.2")
        self.assertTrue(selected["version_matched"])

    def test_lookup_bounds_payload_and_reuses_external_cache(self) -> None:
        (self.root / "package-lock.json").write_text(
            json.dumps({"dependencies": {"react": {"version": "19.1.2"}}}),
            encoding="utf-8",
        )
        candidates = {
            "results": [
                {
                    "id": "/facebook/react",
                    "title": "React",
                    "sourceReputation": "High",
                    "benchmarkScore": 91,
                    "versions": ["v19.1.2"],
                }
            ]
        }
        docs = {
            "codeSnippets": [
                {"codeTitle": "Effect cleanup", "codeList": [{"code": "x" * 5000}]}
            ],
            "infoSnippets": [{"title": "Notes", "content": "y" * 5000}],
        }

        first = context7.lookup(
            self.root,
            "react",
            "How does effect cleanup work?",
            max_chars=1800,
            library_results=candidates,
            docs_results=docs,
        )
        second = context7.lookup(
            self.root,
            "react",
            "How does effect cleanup work?",
            max_chars=1800,
            library_results={"results": []},
            docs_results={},
        )
        larger = context7.lookup(
            self.root,
            "react",
            "How does effect cleanup work?",
            max_chars=2400,
            library_results=candidates,
            docs_results=docs,
        )

        self.assertLessEqual(len(context7.canonical_json(first)), 1800)
        self.assertTrue(first["truncated"])
        self.assertFalse(first["cache"]["hit"])
        self.assertTrue(second["cache"]["hit"])
        self.assertEqual(
            first["provider_response_sha256"], second["provider_response_sha256"]
        )
        self.assertFalse(larger["cache"]["hit"])
        self.assertNotEqual(first["cache"]["path"], larger["cache"]["path"])
        self.assertFalse((self.root / ".cognitive-powers").exists())


if __name__ == "__main__":
    unittest.main()
