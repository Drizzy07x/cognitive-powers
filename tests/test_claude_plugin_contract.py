"""Fail-closed structural contract for the Claude Code host surface.

Claude Code is not required to be installed to run these checks. They validate
the packaging this repository ships against the documented plugin schema and
against the Codex surface, so neither host can drift without a failing test.
"""

from __future__ import annotations

import contextlib
import importlib.util
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import sys
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
# The declared Claude surface. verify_installed.CLAUDE_WORKFLOW_COUNT states
# the same size as a number; this names the members, and the two move together.
SPECIALIZED_SKILLS = {
    "audit-capabilities",
    "communicate-efficiently",
    "design-intentionally",
    "diagnose-systematically",
    "eli5",
    "engineer-prompts",
    "design-review",
    "explore-web-adaptively",
    "legacy-safe-changes",
    "map-project",
    "operate-desktop-adaptively",
    "refactor-cleanly",
    "research-systematically",
    "use-current-docs",
    "verify-installation",
    "verify-web-behavior",
}
# Claude Code truncates description plus when_to_use in the skill listing.
LISTING_CAP = 1536

# Every component path Claude Code discovers without being told to, from the
# file-locations table in the plugin reference. A manifest component field adds
# a custom path; it never replaces this scan. That is the whole reason a second
# host's file parked on one of these is loaded no matter what either manifest
# declares.
CLAUDE_DISCOVERY_PATHS = (
    ".lsp.json",
    ".mcp.json",
    "SKILL.md",
    "agents",
    "bin",
    "commands",
    "hooks/hooks.json",
    "monitors/monitors.json",
    "output-styles",
    "settings.json",
    "skills",
    "themes",
    "workflows",
)
# The only two this plugin means Claude Code to pick up by convention. The
# assertion is equality rather than absence of the known-bad name: what shipped
# broken was not this filename, it was a path being scanned that nobody had
# listed, and only the full convention can catch the next one.
CLAUDE_OWNED_DISCOVERY_PATHS = frozenset({"agents", "skills"})


class DeclaredSurfaceSizeTests(unittest.TestCase):
    def test_verifier_count_matches_the_named_surface(self) -> None:
        """The number and the names are one declaration in two places."""
        spec = importlib.util.spec_from_file_location(
            "verify_installed_surface_size",
            PLUGIN_ROOT / "scripts" / "verify_installed.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        self.assertEqual(
            module.CLAUDE_WORKFLOW_COUNT,
            len(CORE_SKILLS | SPECIALIZED_SKILLS),
        )


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
        for field in ("hooks",):
            value = manifest[field]
            self.assertIsInstance(value, str, f"{field} must be a single path")
            self.assertTrue(value.startswith("./"), f"{field} must start with ./")
            self.assertTrue(
                (PLUGIN_ROOT / value.removeprefix("./")).exists(),
                f"{field} points at a missing path: {value}",
            )

    def test_agents_come_from_the_default_directory(self) -> None:
        """`claude plugin validate --strict` rejects a directory here.

        The field takes agent files and replaces the default scan, while
        `agents/` is already scanned automatically, so declaring it was both
        redundant and malformed.
        """
        self.assertNotIn("agents", load(CLAUDE_MANIFEST))
        self.assertTrue(any((PLUGIN_ROOT / "agents").glob("*.md")))

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


class ClaudeDiscoveryCollisionTests(unittest.TestCase):
    """Nothing a second host owns may sit where Claude Code reads by convention.

    Through 1.9.1 the Codex hook manifest was `hooks/hooks.json`, which is
    Claude Code's default hook location, so it was loaded on top of the
    `hooks.claude.json` the manifest declares and every hook ran twice. It
    stayed invisible on Windows for the reason the duplicate was harmless
    there: the Codex spelling names `python3`, which resolves to the Store stub
    and fails, so the collision read as noise rather than as double injection.

    The rule was already written down for one field -- declaring `skills` would
    put `skills-core/` on top of the default `skills/` scan, and a test says so
    -- and applied to no other. A rule that holds for the field someone
    happened to think about is a rule about that field, not about the scan.
    """

    def test_no_foreign_manifest_sits_on_a_claude_discovery_path(self) -> None:
        present = {
            relative
            for relative in CLAUDE_DISCOVERY_PATHS
            if (PLUGIN_ROOT / relative).exists()
        }
        self.assertEqual(
            present,
            set(CLAUDE_OWNED_DISCOVERY_PATHS),
            "a path Claude Code scans by convention holds something this "
            "plugin never declared for it; add it to the owned set on purpose "
            "or move the file off the scan",
        )

    def test_the_codex_hook_manifest_stays_off_the_scan(self) -> None:
        declared = load(CODEX_MANIFEST)["hooks"].removeprefix("./")
        self.assertNotIn(declared, CLAUDE_DISCOVERY_PATHS)
        self.assertTrue((PLUGIN_ROOT / declared).is_file())

    def test_each_host_carries_its_own_hook_manifest(self) -> None:
        """One file cannot hold both spellings, so neither may be shared.

        Claude Code expands `${CLAUDE_PLUGIN_ROOT}` and takes exec-form `args`;
        Codex expands `$PLUGIN_ROOT` and takes a shell string with a
        `commandWindows` sibling. Each spelling is inert on the other host.
        """
        claude = load(CLAUDE_MANIFEST)["hooks"].removeprefix("./")
        codex = load(CODEX_MANIFEST)["hooks"].removeprefix("./")
        self.assertNotEqual(claude, codex)
        for relative in (claude, codex):
            self.assertTrue((PLUGIN_ROOT / relative).is_file(), relative)


class ClaudeHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hooks = load(CLAUDE_HOOKS)["hooks"]

    def test_declares_only_supported_events(self) -> None:
        self.assertEqual(
            set(self.hooks),
            {"SessionStart", "UserPromptSubmit", "PostToolUse", "Stop"},
        )

    def test_prompt_submit_is_bounded_and_carries_no_matcher(self) -> None:
        entry = self.hooks["UserPromptSubmit"][0]
        self.assertNotIn(
            "matcher", entry, "Claude Code silently ignores a matcher on this event"
        )
        hook = entry["hooks"][0]
        self.assertEqual(hook["args"][0], "${CLAUDE_PLUGIN_ROOT}/hooks/skill_router.py")
        self.assertIsInstance(
            hook.get("timeout"),
            int,
            "the router runs on every prompt, so it must not hold the turn open",
        )
        # UserPromptSubmit already lowers the host default to 30s; ranking a
        # dozen descriptions must stay far below that to be worth the latency.
        self.assertLessEqual(hook["timeout"], 30)

    def test_session_start_is_bounded_and_advisory(self) -> None:
        entry = self.hooks["SessionStart"][0]
        self.assertNotIn(
            "matcher", entry, "the refresh applies to every session source"
        )
        hook = entry["hooks"][0]
        self.assertEqual(
            hook["args"][0], "${CLAUDE_PLUGIN_ROOT}/hooks/semantic_index.py"
        )
        self.assertIsInstance(
            hook.get("timeout"),
            int,
            "an unbounded index refresh would run for the default 600s",
        )
        self.assertLessEqual(hook["timeout"], 300)

    def test_index_refresh_is_isolated_from_evidence_recording(self) -> None:
        """A refresh fault must not reach the script the Stop gate depends on."""
        scripts = {
            hook["args"][0].rsplit("/", 1)[-1]
            for group in self.hooks.values()
            for entry in group
            for hook in entry["hooks"]
        }
        self.assertEqual(
            scripts,
            {
                "clean_code_guard.py",
                "selective_hooks.py",
                "semantic_index.py",
                "skill_activation.py",
                "skill_router.py",
            },
        )

    def test_post_tool_use_matches_claude_file_tools(self) -> None:
        # MultiEdit is absent from current hosts but present in the versions the
        # compatibility matrix still spans; the guard entry beside this one
        # already matched it, and a tool one PostToolUse hook treats as a write
        # while its neighbour ignores it is how a ledger silently loses edits.
        matcher = self.hooks["PostToolUse"][0]["matcher"]
        self.assertEqual(
            set(matcher.split("|")), {"Edit", "Write", "MultiEdit", "NotebookEdit"}
        )

    def test_both_manifests_agree_on_which_tools_are_writes(self) -> None:
        """One tool set per host, and every PostToolUse entry sees all of it.

        The test above pins one entry of one manifest, so the drift it names --
        a tool one hook treats as a write while its neighbour ignores it -- was
        only refused for that entry. It had already happened twice: the guard
        entry beside it missed NotebookEdit, and the Codex manifest missed both
        it and MultiEdit. Nothing could see either, because a matcher that
        names too few tools produces no error, only an event that is never
        recorded. What differs between hosts is the tool vocabulary, so that is
        the only difference this asserts.
        """
        codex = json.loads(
            (PLUGIN_ROOT / "hooks" / "hooks.codex.json").read_text(encoding="utf-8")
        )["hooks"]
        shared = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
        for manifest, expected in (
            (self.hooks, shared),
            (codex, shared | {"apply_patch"}),
        ):
            entries = manifest["PostToolUse"]
            self.assertEqual(len(entries), 2)
            for entry in entries:
                self.assertEqual(set(entry["matcher"].split("|")), expected)

    def test_every_hook_uses_exec_form_against_the_bundled_script(self) -> None:
        entries = [
            hook
            for group in self.hooks.values()
            for entry in group
            for hook in entry["hooks"]
        ]
        self.assertEqual(len(entries), 6)
        for hook in entries:
            self.assertEqual(hook["type"], "command")
            # Shell-form commands reject ${user_config.*}; exec form is required.
            self.assertEqual(hook["command"], "${user_config.python_executable}")
            self.assertIn("args", hook)
            self.assertTrue(
                hook["args"][0].startswith("${CLAUDE_PLUGIN_ROOT}/hooks/"),
                hook["args"][0],
            )
            self.assertNotIn(
                "commandWindows",
                hook,
                "commandWindows is Codex-only and is silently ignored here",
            )
            self.assertNotIn("shell", hook, "exec form must not request a shell")

    def test_subcommands_match_the_scripts_they_invoke(self) -> None:
        declared: dict[str, set[str]] = {}
        for group in self.hooks.values():
            for entry in group:
                for hook in entry["hooks"]:
                    name = hook["args"][0].rsplit("/", 1)[-1]
                    declared.setdefault(name, set()).add(hook["args"][1])
        self.assertEqual(
            declared,
            {
                "clean_code_guard.py": {"post-tool-use"},
                "selective_hooks.py": {"post-tool-use", "stop"},
                "semantic_index.py": {"session-start"},
                "skill_activation.py": {"session-start"},
                "skill_router.py": {"user-prompt-submit"},
            },
        )
        for name, modes in declared.items():
            script = (PLUGIN_ROOT / "hooks" / name).read_text(encoding="utf-8")
            for mode in modes:
                with self.subTest(script=name, mode=mode):
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

    def test_every_installed_skill_is_model_invocable(self) -> None:
        blocked = {
            name
            for name, path in self.skills.items()
            if frontmatter(path).get("disable-model-invocation") is not None
        }
        self.assertEqual(
            blocked,
            set(),
            "Claude Code hides a disable-model-invocation skill from the model "
            "entirely, so it can never be routed to; the core workflows "
            "delegate to the specialized ones by name and would break",
        )

    def test_no_skill_is_told_to_invoke_an_unroutable_skill(self) -> None:
        """The failure this guards is silent: the model simply cannot comply."""
        blocked = {
            name
            for name, path in self.skills.items()
            if frontmatter(path).get("disable-model-invocation") is not None
        }
        for name, path in self.skills.items():
            body = path.read_text(encoding="utf-8")
            for target in blocked:
                if target == name:
                    continue
                with self.subTest(skill=name, target=target):
                    self.assertNotIn(
                        f"`{target}`",
                        body,
                        f"{name} tells the model to use {target}, which the "
                        "host will not expose",
                    )

    def test_every_skill_states_when_to_use_it(self) -> None:
        """Routing quality is the description; a vague one misroutes."""
        for name, path in self.skills.items():
            with self.subTest(skill=name):
                fields = frontmatter(path)
                self.assertTrue(
                    fields.get("when_to_use"),
                    "an explicit trigger contract keeps the model from "
                    "guessing which of 14 workflows applies",
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

    def test_a_workflow_that_declares_itself_read_only_is_held_to_it(self) -> None:
        """These five state a read-only boundary in their own description.

        Until the frontmatter carried it, that boundary was a sentence the model
        was asked to respect while the edit tools stayed in the pool. The field
        is what makes 'audits finished work only' a property of the turn.
        """
        for name in (
            "audit-capabilities",
            "design-review",
            "eli5",
            "verify-delivery",
            "verify-installation",
        ):
            with self.subTest(skill=name):
                path = PLUGIN_ROOT / "skills" / name / "SKILL.md"
                refused = {
                    tool.strip()
                    for tool in frontmatter(path).get("disallowed-tools", "").split(",")
                }
                self.assertEqual(refused, {"Edit", "Write", "NotebookEdit"})

    def test_no_agent_can_spawn_a_descendant(self) -> None:
        """Depth one is a tool-set property or it is nothing.

        Omitting `tools` entirely inherits every tool available to subagents,
        `Agent` included, so the two implementation roles could spawn their own
        workers while the workflow text told them not to.
        """
        for path in sorted((PLUGIN_ROOT / "agents").glob("*.md")):
            with self.subTest(agent=path.stem):
                declared = frontmatter(path).get("tools", "")
                granted = {tool.strip() for tool in declared.split(",")}
                granted.discard("")
                self.assertTrue(
                    granted,
                    "an agent without an explicit tool set inherits Agent and "
                    "can spawn descendants",
                )
                for spawner in ("Agent", "Task"):
                    self.assertNotIn(spawner, granted)

    def test_verifier_cannot_write(self) -> None:
        """Withholding the edit tools is not what makes the verifier read-only.

        This assertion used to stop at three tool names while Bash -- which can
        write, move, and delete -- stayed granted, so it reported a containment
        the packaging never had. Bash cannot be withdrawn without ending the
        agent's ability to run a check, so the guarantee has to come from
        somewhere the agent cannot reach: a disposable worktree.
        """
        fields = frontmatter(PLUGIN_ROOT / "agents" / "verifier.md")
        granted = {tool.strip() for tool in fields.get("tools", "").split(",")}
        granted.discard("")
        self.assertTrue(granted, "the verifier must declare an explicit tool set")
        refused = {
            tool.strip() for tool in fields.get("disallowedTools", "").split(",")
        }
        for forbidden in ("Write", "Edit", "NotebookEdit"):
            self.assertNotIn(forbidden, granted)
            self.assertIn(
                forbidden,
                refused,
                "the edit tools must be refused, not merely unlisted",
            )
        self.assertEqual(
            fields.get("isolation"),
            "worktree",
            "Bash stays granted, so only worktree isolation keeps the user's "
            "tree out of reach of a mutating command",
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

    def test_reports_every_workflow_as_routable(self) -> None:
        claude = next(
            surface
            for surface in self.doctor.host_surfaces(PLUGIN_ROOT)["surfaces"]
            if surface["host"] == "claude-code"
        )
        self.assertEqual(
            set(claude["modelInvocableSkills"]), CORE_SKILLS | SPECIALIZED_SKILLS
        )
        self.assertEqual(claude["userInvocableOnlySkills"], [])
        self.assertEqual(claude["requiredUserConfig"], ["python_executable"])

    def test_a_referenced_but_hidden_skill_is_an_error_finding(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / ".codex-plugin").mkdir()
            (root / ".claude-plugin").mkdir()
            for name in (".codex-plugin", ".claude-plugin"):
                (root / name / "plugin.json").write_text(
                    json.dumps({"name": "fixture", "version": "1.0.0"}),
                    encoding="utf-8",
                )
            for name, extra, body in (
                ("router", "", "Invoke `helper` when the task matches.\n"),
                ("helper", "\ndisable-model-invocation: true", "Do the thing.\n"),
            ):
                skill = root / "skills" / name
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(
                    f"---\nname: {name}\ndescription: d{extra}\n---\n\n{body}",
                    encoding="utf-8",
                )
            findings = self.doctor.host_surfaces(root)["findings"]
        self.assertIn(
            "claude-skill-unroutable", {finding["code"] for finding in findings}
        )

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
        self.assertEqual(
            sorted(surface["exposedSkills"]),
            sorted(CORE_SKILLS | SPECIALIZED_SKILLS),
        )
        self.assertEqual(
            len(surface["internalWorkflows"]), len(CORE_SKILLS | SPECIALIZED_SKILLS)
        )

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


class ClaudeEvidenceRootTests(unittest.TestCase):
    """The hook and the receipt writer must resolve one storage root.

    They run in different processes: the hook is launched by the host, while
    ``work_state.py`` runs as an ordinary tool call. The ``Stop`` gate only
    accepts a receipt stored under the root the hook resolved, so any
    disagreement rejects work that is genuinely complete.
    """

    DATA_VARIABLES = (
        "COGNITIVE_POWERS_DATA",
        "PLUGIN_DATA",
        "CLAUDE_PLUGIN_DATA",
        "CLAUDE_PLUGIN_ROOT",
    )

    # Injected into hook processes by the host, invisible to work_state.py.
    HOST_ONLY_VARIABLES = ("CLAUDE_PLUGIN_DATA", "PLUGIN_DATA")

    def setUp(self) -> None:
        self.hook = self._load("hook", PLUGIN_ROOT / "hooks" / "selective_hooks.py")
        self.durability = self._load(
            "durability",
            PLUGIN_ROOT
            / "skills"
            / "execute-durably"
            / "scripts"
            / "work_state_core"
            / "durability.py",
        )

    @staticmethod
    def _load(name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @contextlib.contextmanager
    def _environment(self, **values: str):
        saved = {key: os.environ.get(key) for key in self.DATA_VARIABLES}
        for key in self.DATA_VARIABLES:
            os.environ.pop(key, None)
        os.environ.update(values)
        try:
            yield
        finally:
            for key, previous in saved.items():
                if previous is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = previous

    def _both_roots(self) -> tuple[Path, Path]:
        roots = self.hook._roots()
        assert roots is not None
        return roots[1], self.durability.resolve_data_root(None)

    def test_agree_when_nothing_is_configured(self) -> None:
        with self._environment():
            hook_root, writer_root = self._both_roots()
        self.assertEqual(hook_root, writer_root)

    def _roots_for(self, **values: str) -> tuple[Path, Path]:
        with self._environment(**values):
            return self._both_roots()

    def test_agree_on_the_shared_variable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            hook_root, writer_root = self._roots_for(COGNITIVE_POWERS_DATA=raw)
        self.assertEqual(hook_root, writer_root)
        self.assertEqual(hook_root, Path(raw).resolve())

    def test_the_configured_root_wins_over_a_host_only_variable(self) -> None:
        """A host-only variable must not displace the configured root.

        Both hosts inject one into hook processes, so the hook sees a value the
        writer never will. Ignoring it is only half the contract: the root the
        operator configured has to survive its presence.
        """
        for variable in self.HOST_ONLY_VARIABLES:
            with self.subTest(variable=variable):
                with (
                    tempfile.TemporaryDirectory() as configured,
                    tempfile.TemporaryDirectory() as injected,
                ):
                    hook_root, writer_root = self._roots_for(
                        COGNITIVE_POWERS_DATA=configured, **{variable: injected}
                    )
                self.assertEqual(hook_root, writer_root)
                self.assertEqual(hook_root, Path(configured).resolve())

    def test_a_host_only_variable_never_splits_the_store(self) -> None:
        """Each host exports a data variable to hook processes only.

        Claude Code exports CLAUDE_PLUGIN_DATA and Codex exports PLUGIN_DATA.
        The receipt writer is not a hook process and cannot see either, so
        reading one in the hook would point the two at different directories.
        """
        for variable in self.HOST_ONLY_VARIABLES:
            with self.subTest(variable=variable):
                with tempfile.TemporaryDirectory() as raw:
                    hook_root, writer_root = self._roots_for(**{variable: raw})
                self.assertEqual(hook_root, writer_root)
                self.assertNotEqual(hook_root, Path(raw).resolve())


class ClaudeStopOutputTests(unittest.TestCase):
    """The Stop warning must reach the party that can act on it.

    Claude Code shows ``systemMessage`` to the user and never to the agent, so
    the warning alone names a gap the agent cannot read. ``additionalContext``
    reaches the agent while leaving the hook fail-open.
    """

    def setUp(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "stop_hook", PLUGIN_ROOT / "hooks" / "selective_hooks.py"
        )
        assert spec is not None and spec.loader is not None
        self.hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.hook)

    def _stop_output(self, *, claude_host: bool) -> dict:
        with tempfile.TemporaryDirectory() as repo_raw:
            with tempfile.TemporaryDirectory() as data_raw:
                repo = Path(repo_raw).resolve()
                (repo / "module.py").write_text("value = 1\n", encoding="utf-8")
                environment = {"COGNITIVE_POWERS_DATA": data_raw}
                if claude_host:
                    environment["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
                else:
                    os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
                saved = {
                    key: os.environ.get(key)
                    for key in ("COGNITIVE_POWERS_DATA", "CLAUDE_PLUGIN_ROOT")
                }
                os.environ.update(environment)
                try:
                    self.hook.post_tool_use(
                        {
                            "session_id": "contract-session",
                            "cwd": str(repo),
                            "hook_event_name": "PostToolUse",
                            "tool_name": "Write",
                            "tool_input": {"file_path": str(repo / "module.py")},
                        }
                    )
                    stream = io.StringIO()
                    with contextlib.redirect_stdout(stream):
                        self.hook.stop(
                            {
                                "session_id": "contract-session",
                                "cwd": str(repo),
                                "hook_event_name": "Stop",
                            }
                        )
                finally:
                    for key, previous in saved.items():
                        if previous is None:
                            os.environ.pop(key, None)
                        else:
                            os.environ[key] = previous
        return json.loads(stream.getvalue() or "{}")

    def test_claude_host_also_tells_the_agent(self) -> None:
        output = self._stop_output(claude_host=True)
        self.assertTrue(output.get("systemMessage"))
        specific = output.get("hookSpecificOutput", {})
        self.assertEqual(specific.get("hookEventName"), "Stop")
        self.assertTrue(specific.get("additionalContext"))

    def test_the_warning_never_blocks_the_turn(self) -> None:
        output = self._stop_output(claude_host=True)
        self.assertNotIn(
            "decision", output, "observability must not stop the conversation"
        )

    def test_other_hosts_receive_no_claude_only_key(self) -> None:
        output = self._stop_output(claude_host=False)
        self.assertTrue(output.get("systemMessage"))
        self.assertNotIn("hookSpecificOutput", output)


class ClaudeSemanticIndexTests(unittest.TestCase):
    """The session-start refresh is advisory and must never fail a session."""

    def setUp(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "semantic_index", PLUGIN_ROOT / "hooks" / "semantic_index.py"
        )
        assert spec is not None and spec.loader is not None
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self._checkout = tempfile.TemporaryDirectory()
        self.root = Path(self._checkout.name).resolve()
        (self.root / ".git").mkdir()
        # The hook refreshes an existing index and never initialises one, so a
        # fixture without this directory exercises the skip path instead.
        (self.root / "graphify-out").mkdir()
        self.addCleanup(self._checkout.cleanup)

    @contextlib.contextmanager
    def _graphify(self, present: bool = True):
        original = self.module.shutil.which
        self.module.shutil.which = lambda name: (
            "/usr/bin/graphify" if present and name == "graphify" else None
        )
        try:
            yield
        finally:
            self.module.shutil.which = original

    def _payload(self, **overrides) -> dict:
        payload = {
            "session_id": "index-session",
            "cwd": str(self.root),
            "hook_event_name": "SessionStart",
            "source": "startup",
        }
        payload.update(overrides)
        return payload

    def test_refreshes_an_existing_checkout(self) -> None:
        calls: list[list[str]] = []

        def runner(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        with self._graphify():
            outcome = self.module.refresh(self._payload(), runner=runner)
        self.assertEqual(outcome["status"], "refreshed")
        self.assertEqual(calls[-1][1:], ["update", str(self.root)])

    def test_it_never_initialises_an_index_that_does_not_exist(self) -> None:
        """Creating one installs a provider into a checkout the user may own."""
        (self.root / "graphify-out").rmdir()
        with self._graphify():
            outcome = self.module.refresh(
                self._payload(),
                runner=lambda *a, **k: self.fail("must not run graphify"),
            )
        self.assertEqual(outcome["status"], "skipped")
        self.assertFalse((self.root / "graphify-out").exists())

    def test_an_unchanged_worktree_is_not_rebuilt(self) -> None:
        """Re-extracting an untouched tree costs seconds for no new answer."""
        stamp = self.root / "graphify-out" / ".cognitive-powers-stamp"

        def runner(argv, **kwargs):
            if argv[0] == "git":
                return subprocess.CompletedProcess(argv, 0, "deadbeef", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with self._graphify():
            first = self.module.refresh(self._payload(), runner=runner)
            self.assertEqual(first["status"], "refreshed")
            self.assertTrue(stamp.is_file())
            second = self.module.refresh(self._payload(), runner=runner)
        self.assertEqual(second["status"], "current")

    def test_a_non_finite_timeout_falls_back(self) -> None:
        """subprocess.run raises OverflowError for inf, breaking never-raise."""
        original = os.environ.get("COGNITIVE_POWERS_INDEX_TIMEOUT")
        os.environ["COGNITIVE_POWERS_INDEX_TIMEOUT"] = "inf"
        try:
            self.assertEqual(
                self.module._timeout(), self.module.DEFAULT_TIMEOUT_SECONDS
            )
        finally:
            if original is None:
                os.environ.pop("COGNITIVE_POWERS_INDEX_TIMEOUT", None)
            else:
                os.environ["COGNITIVE_POWERS_INDEX_TIMEOUT"] = original

    def test_skips_sources_where_nothing_on_disk_moved(self) -> None:
        for source in ("compact", "clear", "fork"):
            with self.subTest(source=source):
                with self._graphify():
                    outcome = self.module.refresh(
                        self._payload(source=source),
                        runner=lambda *a, **k: self.fail("must not run graphify"),
                    )
                self.assertEqual(outcome["status"], "skipped")

    def test_never_indexes_a_directory_that_is_not_a_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as plain:
            with self._graphify():
                outcome = self.module.refresh(
                    self._payload(cwd=plain),
                    runner=lambda *a, **k: self.fail("must not run graphify"),
                )
        self.assertEqual(outcome["status"], "skipped")

    def test_a_missing_provider_is_normal_not_a_fault(self) -> None:
        with self._graphify(present=False):
            outcome = self.module.refresh(self._payload())
        self.assertEqual(outcome["status"], "skipped")

    def test_a_failing_refresh_is_reported_not_raised(self) -> None:
        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 3, "", "boom")

        with self._graphify():
            outcome = self.module.refresh(self._payload(), runner=runner)
        self.assertEqual(outcome["status"], "error")

    def test_a_slow_refresh_is_bounded(self) -> None:
        def runner(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 1)

        with self._graphify():
            outcome = self.module.refresh(self._payload(), runner=runner)
        self.assertEqual(outcome["status"], "timeout")

    def test_the_hook_always_exits_zero(self) -> None:
        stream = io.StringIO()
        stdin = sys.stdin
        sys.stdin = io.TextIOWrapper(io.BytesIO(b"not json"))
        try:
            with contextlib.redirect_stdout(stream):
                code = self.module.main(["session-start"])
        finally:
            sys.stdin = stdin
        self.assertEqual(code, 0)


class ClaudeOfficialValidatorTests(unittest.TestCase):
    """Run the host's own validator when the CLI is present.

    CI is the only place this ran until now, and while CI was unavailable a
    malformed `agents` declaration sat in the manifest undetected: the field
    takes agent files and replaces the default scan, so pointing it at the
    directory that is already scanned was both redundant and invalid.
    """

    def test_the_host_validator_accepts_this_tree(self) -> None:
        executable = shutil.which("claude")
        if not executable:
            self.skipTest("the claude CLI is not installed on this machine")
        completed = subprocess.run(
            [executable, "plugin", "validate", ".", "--strict"],
            cwd=str(PLUGIN_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            (completed.stdout or "") + (completed.stderr or ""),
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
