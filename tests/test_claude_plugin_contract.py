"""Fail-closed structural contract for the Claude Code host surface.

Claude Code is not required to be installed to run these checks. They validate
the packaging this repository ships against the documented plugin schema and
against the Codex surface, so neither host can drift without a failing test.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import re
import tempfile
import unittest
from pathlib import Path

from tests.test_plugin_contract import declared_version


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CLAUDE_MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
CLAUDE_MARKETPLACE = PLUGIN_ROOT / ".claude-plugin" / "marketplace.json"
CODEX_MANIFEST = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
CLAUDE_HOOKS = PLUGIN_ROOT / "hooks" / "hooks.claude.json"

CORE_SKILLS = {"solve-efficiently", "execute-durably", "verify-delivery"}
SPECIALIZED_SKILLS = {
    "audit-capabilities",
    "communicate-efficiently",
    "design-intentionally",
    "diagnose-systematically",
    "engineer-prompts",
    "explore-web-adaptively",
    "map-project",
    "operate-desktop-adaptively",
    "research-systematically",
    "use-current-docs",
    "verify-web-behavior",
}
# Claude Code truncates description plus when_to_use in the skill listing.
LISTING_CAP = 1536
HOST_SPECIFIC_INVOCATION = re.compile(r"\$[a-z][a-z0-9]*(?:-[a-z0-9]+)+\b")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def frontmatter(path: Path) -> dict[str, str]:
    """Parse the leading YAML frontmatter as flat scalar keys."""
    lines = path.read_text(encoding="utf-8").split("\n")
    if not lines or lines[0].strip() != "---":
        raise AssertionError(f"{path}: missing frontmatter opener")
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$", line)
        if match:
            fields[match.group(1)] = match.group(2).strip()
    raise AssertionError(f"{path}: unterminated frontmatter")


class ClaudeManifestTests(unittest.TestCase):
    def test_manifest_exists_and_declares_this_plugin(self) -> None:
        manifest = load(CLAUDE_MANIFEST)
        self.assertEqual(manifest["name"], "cognitive-powers")
        self.assertEqual(manifest["displayName"], "Cognitive Powers")
        self.assertIsInstance(manifest.get("keywords"), list)

    def test_version_never_drifts_across_hosts(self) -> None:
        expected = declared_version()
        claude = load(CLAUDE_MANIFEST)["version"].split("+", 1)[0]
        codex = load(CODEX_MANIFEST)["version"].split("+", 1)[0]
        entry = load(CLAUDE_MARKETPLACE)["plugins"][0]["version"].split("+", 1)[0]
        self.assertEqual(claude, expected, "Claude manifest version drifted")
        self.assertEqual(codex, expected, "Codex manifest version drifted")
        self.assertEqual(entry, expected, "marketplace entry version drifted")

    def test_declared_component_paths_are_relative_and_present(self) -> None:
        manifest = load(CLAUDE_MANIFEST)
        for field in ("agents", "hooks"):
            value = manifest[field]
            self.assertIsInstance(value, str, f"{field} must be a single path")
            self.assertTrue(value.startswith("./"), f"{field} must start with ./")
            self.assertTrue(
                (PLUGIN_ROOT / value.removeprefix("./")).exists(),
                f"{field} points at a missing path: {value}",
            )

    def test_skills_are_discovered_from_the_plugin_root(self) -> None:
        manifest = load(CLAUDE_MANIFEST)
        self.assertNotIn(
            "skills",
            manifest,
            "declaring skills would add skills-core/ on top of the default "
            "skills/ scan and expose duplicate skill names to Claude Code",
        )
        self.assertTrue((PLUGIN_ROOT / "skills").is_dir())
        self.assertFalse((PLUGIN_ROOT / ".claude-plugin" / "skills").exists())

    def test_hook_interpreter_is_user_configured_and_required(self) -> None:
        option = load(CLAUDE_MANIFEST)["userConfig"]["python_executable"]
        self.assertEqual(option["type"], "file")
        self.assertTrue(option["required"])
        self.assertNotIn(
            "default",
            option,
            "no interpreter name resolves on every platform, so there is no "
            "safe default; on Windows python3 is a Microsoft Store stub",
        )
        self.assertIn("Store", option["description"])


class ClaudeHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hooks = load(CLAUDE_HOOKS)["hooks"]

    def test_declares_only_supported_events(self) -> None:
        self.assertEqual(set(self.hooks), {"PostToolUse", "Stop"})

    def test_post_tool_use_matches_claude_file_tools(self) -> None:
        matcher = self.hooks["PostToolUse"][0]["matcher"]
        self.assertEqual(set(matcher.split("|")), {"Edit", "Write", "NotebookEdit"})

    def test_every_hook_uses_exec_form_against_the_bundled_script(self) -> None:
        entries = [
            hook
            for group in self.hooks.values()
            for entry in group
            for hook in entry["hooks"]
        ]
        self.assertEqual(len(entries), 2)
        for hook in entries:
            self.assertEqual(hook["type"], "command")
            # Shell-form commands reject ${user_config.*}; exec form is required.
            self.assertEqual(hook["command"], "${user_config.python_executable}")
            self.assertIn("args", hook)
            self.assertEqual(
                hook["args"][0],
                "${CLAUDE_PLUGIN_ROOT}/hooks/selective_hooks.py",
            )
            self.assertNotIn(
                "commandWindows",
                hook,
                "commandWindows is Codex-only and is silently ignored here",
            )
            self.assertNotIn("shell", hook, "exec form must not request a shell")

    def test_subcommands_match_the_shared_hook_script(self) -> None:
        modes = {
            hook["args"][1]
            for group in self.hooks.values()
            for entry in group
            for hook in entry["hooks"]
        }
        self.assertEqual(modes, {"post-tool-use", "stop"})
        script = (PLUGIN_ROOT / "hooks" / "selective_hooks.py").read_text(
            encoding="utf-8"
        )
        for mode in modes:
            self.assertIn(f'"{mode}"', script)

    def test_hook_script_accepts_claude_tool_names(self) -> None:
        script = (PLUGIN_ROOT / "hooks" / "selective_hooks.py").read_text(
            encoding="utf-8"
        )
        matcher = self.hooks["PostToolUse"][0]["matcher"]
        for tool in matcher.split("|"):
            self.assertIn(
                f'"{tool.lower()}"',
                script,
                f"{tool} is matched but never accepted by selective_hooks.py",
            )
        self.assertIn("CLAUDE_PLUGIN_ROOT", script)


class ClaudeSkillSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skills = {
            path.parent.name: path
            for path in sorted((PLUGIN_ROOT / "skills").glob("*/SKILL.md"))
        }

    def test_skill_directories_match_the_expected_surface(self) -> None:
        self.assertEqual(set(self.skills), CORE_SKILLS | SPECIALIZED_SKILLS)

    def test_frontmatter_name_matches_directory(self) -> None:
        for name, path in self.skills.items():
            with self.subTest(skill=name):
                self.assertEqual(frontmatter(path).get("name"), name)

    def test_only_core_skills_are_model_invocable(self) -> None:
        automatic = {
            name
            for name, path in self.skills.items()
            if frontmatter(path).get("disable-model-invocation") != "true"
        }
        self.assertEqual(
            automatic,
            CORE_SKILLS,
            "Claude Code auto-loads exactly the three core workflows; the "
            "specialized ones stay installed and directly invocable",
        )

    def test_specialized_skills_remain_user_invocable(self) -> None:
        for name in SPECIALIZED_SKILLS:
            with self.subTest(skill=name):
                fields = frontmatter(self.skills[name])
                self.assertNotEqual(
                    fields.get("user-invocable"),
                    "false",
                    "specialized workflows must stay reachable as /name",
                )

    def test_listing_text_stays_within_the_truncation_cap(self) -> None:
        for name, path in self.skills.items():
            with self.subTest(skill=name):
                fields = frontmatter(path)
                listing = fields.get("description", "") + fields.get("when_to_use", "")
                self.assertGreater(len(listing), 0, "description is required")
                self.assertLessEqual(len(listing), LISTING_CAP)

    def test_skill_bodies_carry_no_host_specific_invocation_syntax(self) -> None:
        targets = list((PLUGIN_ROOT / "skills").glob("*/SKILL.md"))
        targets += list((PLUGIN_ROOT / "skills").glob("*/references/*.md"))
        targets += list((PLUGIN_ROOT / "skills-core").glob("*/SKILL.md"))
        for path in targets:
            with self.subTest(path=path.relative_to(PLUGIN_ROOT).as_posix()):
                found = HOST_SPECIFIC_INVOCATION.findall(
                    path.read_text(encoding="utf-8")
                )
                self.assertEqual(
                    found, [], "skill bodies must read identically on both hosts"
                )

    def test_core_skills_anchor_plugin_relative_paths(self) -> None:
        for tree in ("skills", "skills-core"):
            for name in CORE_SKILLS:
                path = PLUGIN_ROOT / tree / name / "SKILL.md"
                with self.subTest(skill=f"{tree}/{name}"):
                    text = path.read_text(encoding="utf-8")
                    self.assertIn("## Locate plugin files", text)
                    self.assertIn(".claude-plugin/plugin.json", text)
                    self.assertIn(".codex-plugin/plugin.json", text)


class ClaudeAgentTests(unittest.TestCase):
    def test_agent_files_mirror_the_codex_roles(self) -> None:
        codex_roles = {
            path.stem for path in (PLUGIN_ROOT / ".codex" / "agents").glob("*.toml")
        }
        claude_roles = {path.stem for path in (PLUGIN_ROOT / "agents").glob("*.md")}
        self.assertEqual(claude_roles, codex_roles)

    def test_agent_frontmatter_is_complete(self) -> None:
        for path in sorted((PLUGIN_ROOT / "agents").glob("*.md")):
            with self.subTest(agent=path.stem):
                fields = frontmatter(path)
                self.assertEqual(fields.get("name"), path.stem)
                self.assertTrue(fields.get("description"))

    def test_verifier_cannot_write(self) -> None:
        tools = frontmatter(PLUGIN_ROOT / "agents" / "verifier.md").get("tools", "")
        granted = {tool.strip() for tool in tools.split(",") if tool.strip()}
        self.assertTrue(granted, "the verifier must declare an explicit tool set")
        for forbidden in ("Write", "Edit", "NotebookEdit"):
            self.assertNotIn(
                forbidden, granted, "read-only verification cannot mutate the workspace"
            )


class ClaudeDoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "doctor", PLUGIN_ROOT / "scripts" / "doctor.py"
        )
        assert spec is not None and spec.loader is not None
        self.doctor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.doctor)

    def test_reports_both_surfaces_without_probing_a_host(self) -> None:
        hosts = self.doctor.host_surfaces(PLUGIN_ROOT)
        self.assertFalse(hosts["probed"])
        self.assertTrue(hosts["versionsAligned"])
        self.assertEqual(hosts["findings"], [])
        by_host = {surface["host"]: surface for surface in hosts["surfaces"]}
        self.assertEqual(set(by_host), {"codex", "claude-code"})
        for surface in by_host.values():
            self.assertTrue(surface["present"])
            self.assertNotIn("error", surface)

    def test_reports_the_claude_invocation_split(self) -> None:
        claude = next(
            surface
            for surface in self.doctor.host_surfaces(PLUGIN_ROOT)["surfaces"]
            if surface["host"] == "claude-code"
        )
        self.assertEqual(set(claude["modelInvocableSkills"]), CORE_SKILLS)
        self.assertEqual(set(claude["userInvocableOnlySkills"]), SPECIALIZED_SKILLS)
        self.assertEqual(claude["requiredUserConfig"], ["python_executable"])

    def test_version_drift_between_hosts_is_an_error_finding(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / ".codex-plugin").mkdir()
            (root / ".claude-plugin").mkdir()
            (root / "skills").mkdir()
            (root / ".codex-plugin" / "plugin.json").write_text(
                json.dumps({"name": "fixture", "version": "1.0.0"}), encoding="utf-8"
            )
            (root / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": "fixture", "version": "9.9.9"}), encoding="utf-8"
            )
            hosts = self.doctor.host_surfaces(root)
        self.assertFalse(hosts["versionsAligned"])
        self.assertIn(
            "host-version-drift", {finding["code"] for finding in hosts["findings"]}
        )

    def test_missing_claude_manifest_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / ".codex-plugin").mkdir()
            (root / ".codex-plugin" / "plugin.json").write_text(
                json.dumps({"name": "fixture", "version": "1.0.0"}), encoding="utf-8"
            )
            hosts = self.doctor.host_surfaces(root)
        self.assertIn(
            "claude-manifest-missing",
            {finding["code"] for finding in hosts["findings"]},
        )


class ClaudeVerifyInstalledTests(unittest.TestCase):
    def setUp(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "verify_installed", PLUGIN_ROOT / "scripts" / "verify_installed.py"
        )
        assert spec is not None and spec.loader is not None
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_this_checkout_presents_the_expected_claude_surface(self) -> None:
        version = load(CLAUDE_MANIFEST)["version"]
        surface = self.module._claude_surface(PLUGIN_ROOT, version)
        self.assertTrue(surface["matched"], surface)
        self.assertEqual(surface["host"], "claude-code")
        self.assertEqual(sorted(surface["exposedSkills"]), sorted(CORE_SKILLS))
        self.assertEqual(len(surface["internalWorkflows"]), 14)

    def test_surface_fails_closed_on_version_drift(self) -> None:
        surface = self.module._claude_surface(PLUGIN_ROOT, "0.0.0")
        self.assertFalse(surface["matched"])

    def test_surface_fails_closed_without_a_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            surface = self.module._claude_surface(Path(raw), "1.0.0")
        self.assertFalse(surface["matched"])
        self.assertIn("error", surface)

    def test_model_invocable_detection_honours_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for name, disabled in (("auto", False), ("manual", True)):
                skill = root / "skills" / name
                skill.mkdir(parents=True)
                flag = "\ndisable-model-invocation: true" if disabled else ""
                (skill / "SKILL.md").write_text(
                    f"---\nname: {name}\ndescription: d{flag}\n---\n\nbody\n",
                    encoding="utf-8",
                )
            automatic = self.module._model_invocable_skills(root)
        self.assertEqual(automatic, ["auto"])

    def test_claude_host_never_claims_host_inventory(self) -> None:
        source = inspect.getsource(self.module.verify_installation)
        self.assertIn('"hostInventoryVerified": False', source)
        self.assertIn("claude-code", self.module.SUPPORTED_HOSTS)

    def test_unsupported_host_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.module.verify_installation(
                PLUGIN_ROOT, PLUGIN_ROOT, "v0.0.0", host="cursor"
            )


class ClaudeCiTests(unittest.TestCase):
    def test_ci_runs_the_official_plugin_validator(self) -> None:
        workflow = (PLUGIN_ROOT / ".github" / "workflows" / "validate.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("@anthropic-ai/claude-code", workflow)
        self.assertIn("claude plugin validate . --strict", workflow)


class ClaudeMarketplaceTests(unittest.TestCase):
    def test_marketplace_publishes_this_tree_once(self) -> None:
        marketplace = load(CLAUDE_MARKETPLACE)
        self.assertEqual(marketplace["name"], "cognitive-powers")
        self.assertTrue(marketplace["owner"]["name"])
        self.assertEqual(len(marketplace["plugins"]), 1)

        entry = marketplace["plugins"][0]
        self.assertEqual(entry["name"], load(CLAUDE_MANIFEST)["name"])
        self.assertEqual(entry["source"], "./")


if __name__ == "__main__":
    unittest.main()
