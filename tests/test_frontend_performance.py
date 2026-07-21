from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT
    / "skills"
    / "design-intentionally"
    / "scripts"
    / "frontend_performance.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "test_frontend_performance_module", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


frontend = load_module()


class FrontendPerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_package(self, dependencies: dict[str, str]) -> None:
        (self.root / "package.json").write_text(
            json.dumps({"dependencies": dependencies}), encoding="utf-8"
        )

    def test_next_audit_reports_version_bound_candidates_without_quality_claims(
        self,
    ) -> None:
        self.write_package({"next": "16.2.0", "react": "19.2.0", "three": "0.180.0"})
        app = self.root / "app"
        app.mkdir()
        (app / "layout.tsx").write_text(
            "'use client';\n"
            "import * as THREE from 'three';\n"
            "export default function Layout(){return <><img src='/x.png'/><script src='https://x.test/a.js'/></>}\n",
            encoding="utf-8",
        )

        result = frontend.audit(self.root)

        self.assertEqual(
            result["frameworkVersions"], {"next": "16.2.0", "react": "19.2.0"}
        )
        self.assertEqual(result["scannedFiles"], 1)
        self.assertEqual(
            {finding["rule"] for finding in result["findings"]},
            {
                "heavy-client-import",
                "next-raw-img",
                "next-raw-script",
                "next-root-client-boundary",
            },
        )
        self.assertFalse(result["measuredRuntimePerformance"])
        self.assertFalse(result["optimizationProven"])

    def test_dependencies_and_build_outputs_are_not_scanned(self) -> None:
        self.write_package({"react": "19.2.0"})
        source = self.root / "src"
        dependency = self.root / "node_modules" / "pkg"
        source.mkdir()
        dependency.mkdir(parents=True)
        (source / "view.tsx").write_text(
            "export const View = () => <div />;", encoding="utf-8"
        )
        (dependency / "bad.tsx").write_text(
            "'use client'; import 'three';", encoding="utf-8"
        )

        result = frontend.audit(self.root)

        self.assertEqual(result["scannedFiles"], 1)
        self.assertEqual(result["findings"], [])

    def test_non_react_project_is_rejected(self) -> None:
        self.write_package({"vue": "3.5.0"})
        with self.assertRaises(frontend.FrontendAuditError):
            frontend.audit(self.root)

    def test_leading_comments_preserve_client_detection_and_comment_tags_are_ignored(
        self,
    ) -> None:
        self.write_package({"next": "16.2.0", "react": "19.2.0", "three": "0.180.0"})
        app = self.root / "app"
        app.mkdir()
        (app / "layout.tsx").write_text(
            "/* license */\n"
            "// component boundary follows\n"
            "'use client';\n"
            "import * as THREE from 'three';\n"
            "/* <img src='/not-real.png'/> */\n"
            "const sample = \"<script src='not-real.js'>\";\n"
            "export default function Layout(){return <main />;}\n",
            encoding="utf-8",
        )

        result = frontend.audit(self.root)

        rules = [finding["rule"] for finding in result["findings"]]
        self.assertEqual(rules, ["next-root-client-boundary", "heavy-client-import"])

    def test_cli_warning_gate_uses_distinct_exit_code(self) -> None:
        self.write_package({"next": "16.2.0", "react": "19.2.0"})
        app = self.root / "app"
        app.mkdir()
        (app / "layout.tsx").write_text(
            "'use client'; export default function L(){return null}", encoding="utf-8"
        )

        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--root",
                str(self.root),
                "--fail-on-warning",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 1, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["summary"]["warning"], 1)
        self.assertFalse(payload["optimizationProven"])

    def test_template_and_regex_text_do_not_become_findings(self) -> None:
        self.write_package({"next": "16.2.0", "react": "19.2.0", "three": "0.180.0"})
        source = self.root / "src"
        source.mkdir()
        (source / "view.tsx").write_text(
            "'use client';\n"
            "const sample = `import Fake from 'three'`;\n"
            "const tagPattern = /<img\\b/;\n"
            "const scriptPattern = () => /<script\\b/i;\n"
            "export function View(){return <main />;}\n",
            encoding="utf-8",
        )

        result = frontend.audit(self.root)

        self.assertEqual(result["findings"], [])


if __name__ == "__main__":
    unittest.main()
