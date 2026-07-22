from __future__ import annotations

import json
import re
import struct
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


class PluginContractTests(unittest.TestCase):
    def test_manifest_identity_and_declared_paths(self) -> None:
        manifest_path = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertIn(manifest["name"], {PLUGIN_ROOT.name, PLUGIN_ROOT.parent.name})
        self.assertEqual(manifest["version"].split("+", 1)[0], "1.4.2")
        self.assertEqual(manifest["skills"], "./skills-core/")
        self.assertEqual(manifest["hooks"], "./hooks/hooks.json")
        self.assertTrue((PLUGIN_ROOT / "skills-core").is_dir())
        self.assertEqual(
            {
                path.parent.name
                for path in (PLUGIN_ROOT / "skills-core").glob("*/SKILL.md")
            },
            {"solve-efficiently", "execute-durably", "verify-delivery"},
        )
        prompts = manifest["interface"]["defaultPrompt"]
        self.assertLessEqual(len(prompts), 3)
        self.assertTrue(all(len(prompt) <= 128 for prompt in prompts))
        self.assertEqual(
            manifest["interface"]["screenshots"],
            [],
            "screenshots require a verified public host surface",
        )

    def test_private_marketplace_points_to_plugin_root(self) -> None:
        marketplace_path = PLUGIN_ROOT / ".agents" / "plugins" / "marketplace.json"
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
        self.assertEqual(marketplace["name"], "cognitive-powers")
        self.assertEqual(marketplace["interface"]["displayName"], "Cognitive Powers")
        self.assertEqual(len(marketplace["plugins"]), 1)

        entry = marketplace["plugins"][0]
        self.assertEqual(entry["name"], "cognitive-powers")
        self.assertEqual(entry["source"], {"source": "local", "path": "./"})
        self.assertEqual(
            entry["policy"],
            {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        )
        self.assertEqual(entry["category"], "Productivity")

    def test_skill_resources_are_reachable(self) -> None:
        expected = [
            "skills/solve-efficiently/SKILL.md",
            "skills/solve-efficiently/references/routing.md",
            "skills/solve-efficiently/references/context-economy.md",
            "skills/solve-efficiently/references/semantic-navigation.md",
            "skills/solve-efficiently/scripts/semantic_context.py",
            "skills/solve-efficiently/scripts/semantic_provider.py",
            "skills/solve-efficiently/scripts/memory_context.py",
            "skills/solve-efficiently/scripts/context_pipeline.py",
            "skills/solve-efficiently/scripts/orchestration_policy.py",
            "skills/audit-capabilities/SKILL.md",
            "skills/audit-capabilities/agents/openai.yaml",
            "skills/audit-capabilities/references/evidence-contract.md",
            "skills/audit-capabilities/scripts/capability_audit.py",
            "skills/audit-capabilities/scripts/capability_lifecycle.py",
            "skills/diagnose-systematically/SKILL.md",
            "skills/diagnose-systematically/agents/openai.yaml",
            "skills/diagnose-systematically/references/feedback-loops.md",
            "skills/diagnose-systematically/references/investigation-lanes.md",
            "skills/diagnose-systematically/scripts/investigation_protocol.py",
            "skills/use-current-docs/SKILL.md",
            "skills/use-current-docs/references/evidence-contract.md",
            "skills/solve-efficiently/scripts/context_lens.py",
            "skills/use-current-docs/SKILL.md",
            "skills/use-current-docs/agents/openai.yaml",
            "skills/use-current-docs/references/evidence-contract.md",
            "skills/use-current-docs/scripts/context7_lookup.py",
            "benchmarks/external_context_cases.json",
            "skills/map-project/SKILL.md",
            "skills/map-project/agents/openai.yaml",
            "skills/map-project/references/domain-glossary.md",
            "skills/execute-durably/SKILL.md",
            "skills/execute-durably/agents/openai.yaml",
            "skills/execute-durably/references/state-contract.md",
            "skills/execute-durably/references/work-packets.md",
            "skills/execute-durably/references/agent-roles.md",
            "skills/execute-durably/references/hook-evidence.md",
            "skills/execute-durably/scripts/plan_compiler.py",
            "skills/execute-durably/scripts/work_state.py",
            "skills/execute-durably/scripts/coordination_report.py",
            "skills/verify-delivery/SKILL.md",
            "skills/verify-delivery/references/evidence-standard.md",
            "skills/verify-delivery/references/evaluation-protocol.md",
            "skills/verify-delivery/references/finding-contract.md",
            "skills/verify-delivery/references/review-angles.md",
            "skills/verify-delivery/references/two-axis-review.md",
            "skills/verify-delivery/scripts/review_protocol.py",
            "skills/verify-delivery/scripts/knowledge_closeout.py",
            "skills/research-systematically/SKILL.md",
            "skills/research-systematically/agents/openai.yaml",
            "skills/research-systematically/references/protocol.md",
            "skills/research-systematically/scripts/research_protocol.py",
            "skills/verify-web-behavior/SKILL.md",
            "skills/verify-web-behavior/agents/openai.yaml",
            "skills/verify-web-behavior/references/evidence-contract.md",
            "skills/verify-web-behavior/scripts/browser_evidence.py",
            "skills/operate-desktop-adaptively/SKILL.md",
            "skills/operate-desktop-adaptively/agents/openai.yaml",
            "skills/operate-desktop-adaptively/references/evidence-contract.md",
            "skills/operate-desktop-adaptively/scripts/qcu_evidence.py",
            "skills/explore-web-adaptively/SKILL.md",
            "skills/explore-web-adaptively/agents/openai.yaml",
            "skills/explore-web-adaptively/references/navigation-contract.md",
            "skills/explore-web-adaptively/scripts/skyvern_evidence.py",
            "skills/communicate-efficiently/SKILL.md",
            "skills/communicate-efficiently/agents/openai.yaml",
            "skills/communicate-efficiently/references/communication-contract.md",
            "skills/communicate-efficiently/scripts/communication_contract.py",
            "skills/design-intentionally/SKILL.md",
            "skills/design-intentionally/agents/openai.yaml",
            "skills/design-intentionally/references/design-intent-contract.md",
            "skills/design-intentionally/references/greenfield.md",
            "skills/design-intentionally/references/redesign.md",
            "skills/design-intentionally/references/visual-verification.md",
            "skills/design-intentionally/scripts/design_intent.py",
            "skills/design-intentionally/scripts/design_evidence.py",
            "skills/design-intentionally/scripts/frontend_performance.py",
            "skills/design-intentionally/references/frontend-performance.md",
            "skills/engineer-prompts/SKILL.md",
            "skills/engineer-prompts/agents/openai.yaml",
            "skills/engineer-prompts/scripts/prompt_contract.py",
            ".codex/agents/executor.toml",
            ".codex/agents/test-writer.toml",
            ".codex/agents/verifier.toml",
            "hooks/hooks.json",
            "hooks/selective_hooks.py",
            "scripts/validate_skills.py",
            "scripts/run_semantic_benchmarks.py",
            "scripts/run_browser_benchmarks.py",
            "scripts/run_qcu_benchmarks.py",
            "scripts/run_skyvern_benchmarks.py",
            "scripts/run_communication_benchmarks.py",
            "scripts/run_design_benchmarks.py",
            "scripts/run_capability_benchmarks.py",
            "scripts/run_coordination_benchmarks.py",
            "scripts/orchestration_policy.py",
            "scripts/run_extension_benchmarks.py",
            "scripts/run_skill_routing_benchmarks.py",
            "scripts/run_memory_benchmarks.py",
            "scripts/external_catalog.py",
            "scripts/integration_adapters.py",
            "scripts/integration_evaluation.py",
            "scripts/release_witness.py",
            "scripts/validate_all.py",
            "scripts/doctor.py",
            "benchmarks/semantic_cases.json",
            "benchmarks/browser_cases.json",
            "benchmarks/qcu_cases.json",
            "benchmarks/fixtures/qcu-run/valid.json",
            "benchmarks/fixtures/qcu-run/stale.json",
            "benchmarks/fixtures/qcu-run/incomplete.json",
            "benchmarks/skyvern_cases.json",
            "benchmarks/communication_cases.json",
            "benchmarks/design_cases.json",
            "benchmarks/capability_cases.json",
            "benchmarks/coordination_cases.json",
            "benchmarks/agent_plan_cases.json",
            "benchmarks/controller_ab_protocol.json",
            "benchmarks/extension_cases.json",
            "benchmarks/skill_routing_cases.json",
            "benchmarks/memory_cases.json",
            "benchmarks/semantic_provider_cases.json",
            "benchmarks/integration_evaluation_cases.json",
            "benchmarks/baseline-1.1.0.json",
            "integrations/catalog.json",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
        ]
        missing = [path for path in expected if not (PLUGIN_ROOT / path).is_file()]
        self.assertEqual(missing, [])

    def test_core_router_targets_installed_internal_workflows(self) -> None:
        targets: list[str] = []
        pattern = r"`(\.\./\.\./skills/[^`]+/SKILL\.md)`"
        for skill_file in (PLUGIN_ROOT / "skills-core").glob("*/SKILL.md"):
            text = skill_file.read_text(encoding="utf-8")
            for target in re.findall(pattern, text):
                targets.append(target)
                self.assertTrue(
                    (skill_file.parent / target).resolve().is_file(), target
                )
        self.assertGreaterEqual(len(set(targets)), 14)

    def test_brand_assets_are_valid_transparent_pngs(self) -> None:
        expected_dimensions = {
            "assets/logo.png": (1100, 480),
            "assets/icon.png": (512, 512),
        }
        for relative, expected_size in expected_dimensions.items():
            path = PLUGIN_ROOT / relative
            self.assertTrue(path.is_file(), relative)
            with path.open("rb") as image:
                self.assertEqual(image.read(8), b"\x89PNG\r\n\x1a\n")
                length = struct.unpack(">I", image.read(4))[0]
                self.assertEqual(image.read(4), b"IHDR")
                ihdr = image.read(length)
            width, height, bit_depth, color_type = struct.unpack(">IIBB", ihdr[:10])
            self.assertEqual((width, height), expected_size)
            self.assertEqual(bit_depth, 8)
            self.assertEqual(color_type, 6, "asset must use RGBA transparency")

    def test_no_scaffold_placeholders_remain(self) -> None:
        checked_suffixes = {".md", ".json", ".yaml", ".py"}
        placeholder_marker = "[" + "TODO:"
        placeholders: list[str] = []
        for path in PLUGIN_ROOT.rglob("*"):
            if path.is_file() and path.suffix in checked_suffixes:
                text = path.read_text(encoding="utf-8")
                if placeholder_marker in text:
                    placeholders.append(path.relative_to(PLUGIN_ROOT).as_posix())
        self.assertEqual(placeholders, [])

    def test_readme_exposes_reproducible_entrypoints_and_limitations(self) -> None:
        text = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
        required_sections = [
            "## Quickstart: three flows",
            "## Doctor",
            "## Capability matrix",
            "## Evaluation protocol",
            "## Live evidence limitations",
        ]
        for section in required_sections:
            self.assertIn(section, text)
        self.assertIn("scripts/validate_all.py --offline", text)
        self.assertIn("scripts/doctor.py --validate-installation", text)
        self.assertIn("repos/Drizzy07x/cognitive-powers/contents/install.ps1", text)
        self.assertIn("| Out-String", text)
        self.assertIn("cognitive-powers@cognitive-powers", text)
        self.assertIn("codex plugin add cognitive-powers@personal --json", text)
        self.assertIn("codex plugin list --json", text)
        self.assertIn("No product screenshots are claimed", text)

    def test_private_github_installer_is_fail_closed_and_version_pinned(self) -> None:
        installer = (PLUGIN_ROOT / "install.ps1").read_text(encoding="utf-8")
        required_fragments = [
            "Set-StrictMode -Version Latest",
            '$ErrorActionPreference = "Stop"',
            '$repository = "Drizzy07x/cognitive-powers"',
            '$pluginId = "cognitive-powers@cognitive-powers"',
            '$expectedVersion = "1.4.2"',
            '"auth", "setup-git"',
            '"plugin", "marketplace", "add"',
            '"plugin", "marketplace", "upgrade"',
            '"plugin", "add", $pluginId',
        ]
        for fragment in required_fragments:
            self.assertIn(fragment, installer)

        self.assertNotIn("gho_", installer)
        self.assertNotIn("github_pat_", installer)

    def test_windows_docs_do_not_invoke_unresolved_python_alias(self) -> None:
        documented_entrypoints = [
            "README.md",
            "skills/map-project/SKILL.md",
            "skills/execute-durably/SKILL.md",
            "skills/solve-efficiently/references/context-economy.md",
        ]
        unresolved: list[str] = []
        for relative in documented_entrypoints:
            text = (PLUGIN_ROOT / relative).read_text(encoding="utf-8")
            if re.search(r"(?m)^python(?:\.exe)?\s", text):
                unresolved.append(relative)
        self.assertEqual(unresolved, [])


if __name__ == "__main__":
    unittest.main()
