from __future__ import annotations

import json
import re
import struct
import subprocess
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def declared_version() -> str:
    """Return the newest dated changelog release, the single version source."""
    text = (PLUGIN_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(
        r"^## (\d+\.\d+\.\d+) - \d{4}-\d{2}-\d{2}\s*$", text, re.MULTILINE
    )
    if match is None:
        raise AssertionError("CHANGELOG.md has no dated release heading")
    return match.group(1)


class PluginContractTests(unittest.TestCase):
    def test_manifest_identity_and_declared_paths(self) -> None:
        manifest_path = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["name"], "cognitive-powers")
        self.assertEqual(manifest["version"].split("+", 1)[0], declared_version())
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
        ignored = (PLUGIN_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("graphify-out/", ignored)

    def test_documented_tags_match_the_declared_version(self) -> None:
        expected = f"v{declared_version()}"
        pattern = re.compile(r"v\d+\.\d+\.\d+")
        previous = {
            f"v{version}"
            for version in re.findall(
                r"^## (\d+\.\d+\.\d+) - ",
                (PLUGIN_ROOT / "CHANGELOG.md").read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        }
        for relative in ("README.md", "docs/operations.md", "install.ps1"):
            text = (PLUGIN_ROOT / relative).read_text(encoding="utf-8")
            for found in pattern.findall(text):
                # Rollback instructions legitimately name an older release.
                if found in previous and found != expected:
                    continue
                with self.subTest(document=relative, tag=found):
                    self.assertEqual(
                        found,
                        expected,
                        f"{relative} documents {found} but the declared release "
                        f"is {expected}",
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
            "scripts/skill_routing.py",
            "scripts/run_memory_benchmarks.py",
            "scripts/external_catalog.py",
            "scripts/integration_adapters.py",
            "scripts/integration_evaluation.py",
            "scripts/finalize_controller_ab_evidence.py",
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
            '$pluginName = "cognitive-powers"',
            f'[string]$ReleaseRef = "v{declared_version()}"',
            "$releaseRef = $ReleaseRef",
            "$expectedVersion = $releaseRef.Substring(1)",
            "$allowedSources = @(",
            "[string]::IsNullOrWhiteSpace($configuredSource)",
            "$allowedSources -notcontains $configuredSource",
            '"plugin", "marketplace", "remove", $marketplace',
            '"plugin", "marketplace", "add", $repository, "--ref", $releaseCommit',
            '& gh api "repos/$repository/commits/$releaseRef"',
            "$releaseCommit -notmatch '^[0-9a-f]{40}$'",
            '"auth", "setup-git"',
            '"api", "repos/$repository/git/ref/tags/$releaseRef", "--silent"',
            '"plugin", "marketplace", "add"',
            '"plugin", "add", $pluginId',
            '"plugin", "remove", $previous.pluginId',
            "$rollbackRoot",
            "$rollbackMarketplace",
            "$allowedPreviousPluginIds",
            "$configuredSourceIsPinnedRepository",
            "$configuredSourceIsRecoveryMarketplace",
            "'^rollback-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'",
            "$unknownDuplicates",
            "$personalMarketplace",
            "$provisionalMatches",
            "$restoredMatches.Count -ne $duplicates.Count",
            "$restoredMarketplace[0].root",
            "Copy-Item -LiteralPath $configured[0].root",
            '"plugin", "marketplace", "add", $rollbackMarketplace',
            '"plugin", "add", $previous.pluginId',
            "function Read-CodexJsonBestEffort",
            '$ErrorActionPreference = "SilentlyContinue"',
            "catch {",
            "$enabledMatches.Count -ne 1",
        ]
        for fragment in required_fragments:
            self.assertIn(fragment, installer)

        self.assertNotIn("gho_", installer)
        self.assertNotIn("github_pat_", installer)
        self.assertNotIn('"--ref", "main"', installer)
        readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("rollback", readme.lower())
        # The rollback target must be a release that actually exists as a tag.
        # CHANGELOG headings are not that: 1.6.0 and 1.7.0 were described there
        # but never tagged, so deriving "the prior release" from heading order
        # made this test enforce a documented command that throws immediately.
        published = json.loads(
            (PLUGIN_ROOT / "docs" / "releases.json").read_text(encoding="utf-8")
        )["published"]
        self.assertTrue(published, "docs/releases.json lists no published release")
        parsed = []
        for tag in published:
            match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag)
            self.assertIsNotNone(match, f"malformed release tag: {tag}")
            parsed.append(tuple(int(part) for part in match.groups()))
        self.assertEqual(
            parsed,
            sorted(parsed, reverse=True),
            "docs/releases.json must list tags newest first",
        )
        declared = tuple(int(part) for part in declared_version().split("."))
        rollback_targets = [
            tag for tag, version in zip(published, parsed) if version < declared
        ]
        self.assertTrue(
            rollback_targets,
            "no published release below the declared version to roll back to",
        )
        self.assertIn(
            f"-ReleaseRef {rollback_targets[0]}",
            readme,
            "the documented rollback must name the newest published release "
            "below the declared version",
        )

    def test_published_releases_are_real_tags(self) -> None:
        """docs/releases.json must never drift from the tags that exist.

        Offline and fork-safe: it runs only where a Git checkout with tags is
        available, and skips cleanly on archives.
        """
        if not (PLUGIN_ROOT / ".git").exists():
            self.skipTest("not a Git checkout")
        completed = subprocess.run(
            ["git", "-C", str(PLUGIN_ROOT), "tag", "--list", "v*"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode != 0:
            self.skipTest("git tags are unavailable")
        local_tags = {line.strip() for line in completed.stdout.splitlines() if line}
        if not local_tags:
            self.skipTest("this clone carries no release tags")
        published = json.loads(
            (PLUGIN_ROOT / "docs" / "releases.json").read_text(encoding="utf-8")
        )["published"]
        missing = [tag for tag in published if tag not in local_tags]
        self.assertEqual(
            missing,
            [],
            f"docs/releases.json lists tags this repository does not have: {missing}",
        )

    def test_lint_target_matches_the_lowest_supported_python(self) -> None:
        """Bind the linter's parse target to the floor the matrix declares.

        Syntax newer than the floor fails at import on the oldest matrix cell,
        before any assertion in that file can run, and the author's own
        interpreter never sees it. A routing benchmark shipped a backslash
        inside an f-string replacement field, which is a syntax error until
        3.12, and every 3.11 cell died on it. ``ruff check`` already runs over
        the whole tree in the validation suite, so configuring its target is
        what makes that reachable without CI. ``ast.feature_version`` is not an
        alternative: it does not roll back the 3.12 f-string tokenizer and
        accepts the very line that broke 3.11.
        """
        workflow = (PLUGIN_ROOT / ".github" / "workflows" / "validate.yml").read_text(
            encoding="utf-8"
        )
        declared = re.search(r"^\s*python:\s*\[(.+?)\]\s*$", workflow, re.MULTILINE)
        self.assertIsNotNone(declared, "the workflow declares no python axis")
        versions = sorted(
            tuple(int(part) for part in value.strip().strip("\"'").split("."))
            for value in declared.group(1).split(",")
        )
        self.assertTrue(versions, "the python axis is empty")
        floor = versions[0]
        self.assertEqual(len(floor), 2, "the python axis must be major.minor")

        configured = re.search(
            r'^\s*target-version\s*=\s*"py(\d)(\d+)"\s*$',
            (PLUGIN_ROOT / "ruff.toml").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        self.assertIsNotNone(configured, "ruff.toml declares no target-version")
        self.assertEqual(
            (int(configured.group(1)), int(configured.group(2))),
            floor,
            "ruff.toml must parse against the lowest python the matrix declares",
        )

    def test_tag_ci_requires_exact_release_witness(self) -> None:
        workflow = (PLUGIN_ROOT / ".github" / "workflows" / "validate.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("startsWith(github.ref, 'refs/tags/')", workflow)
        self.assertIn("scripts/release_witness.py", workflow)
        self.assertIn("./install.ps1 -ReleaseRef", workflow)
        self.assertIn("scripts/verify_installed.py --source-root", workflow)
        self.assertIn("--installation", workflow)
        self.assertIn("--verified-receipt-sha256", workflow)
        self.assertIn('identity.get("runId")', workflow)
        self.assertIn("--receipt", workflow)
        self.assertIn("npm ci --prefix", workflow)
        self.assertIn("--require-hashes", workflow)
        self.assertIn("merge-multiple: false", workflow)
        self.assertIn("aggregate_release_artifacts.py", workflow)
        self.assertIn('test "${#receipts[@]}" -eq 108', workflow)
        self.assertIn("'compatible': 108", workflow)
        self.assertIn("$marketplaceState.marketplaces", workflow)
        self.assertIn(
            "validation-${{ matrix.os }}-py${{ matrix.python }}-codex${{ matrix.codex }}.json",
            workflow,
        )
        self.assertTrue(
            (PLUGIN_ROOT / "ci" / "codex-0.145.0" / "package-lock.json").is_file()
        )
        self.assertTrue(
            (
                PLUGIN_ROOT / "ci" / "codex-0.146.0-alpha.3.1" / "package-lock.json"
            ).is_file()
        )
        self.assertNotRegex(workflow, r"actions/[a-z-]+@v[0-9]+")
        for action_sha in (
            "11d5960a326750d5838078e36cf38b85af677262",
            "a26af69be951a213d495a4c3e4e4022e16d87065",
            "49933ea5288caeca8642d1e84afbd3f7d6820020",
            "ea165f8d65b6e75b540449e92b4886f43607fa02",
            "d3f86a106a0bac45b974a628896c90dbdf5c8093",
            "e8998f949152b193b063cb0ec769d69d929409be",
        ):
            self.assertIn(action_sha, workflow)
        post_release = (
            PLUGIN_ROOT / ".github" / "workflows" / "verify-release.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("gh release download", post_release)
        self.assertIn("sha256sum --check", post_release)
        self.assertIn("gh attestation verify", post_release)
        self.assertIn("len(matrix['rows']) == 108", post_release)
        self.assertNotRegex(post_release, r"actions/[a-z-]+@v[0-9]+")
        # Downloads and rebuilds live outside the checkout: ./assets is
        # tracked artwork, and downloading into the working tree is the
        # publisher defect this job must not copy.
        self.assertIn('--dir "${{ runner.temp }}/release-download"', post_release)
        self.assertNotIn("--dir release-download", post_release)
        self.assertIn('find "$DOWNLOAD" -maxdepth 1', post_release)
        # The mismatch paths must say what they observed; a bare test here is
        # indistinguishable from every other silent failure in the job.
        self.assertIn("does not match the allowlist", post_release)
        self.assertIn("does not reproduce from the tagged source", post_release)
        # Post-publish, the body must equal the changelog section, and the
        # job fires on publication rather than waiting to be remembered.
        self.assertIn("release_identity.py", post_release)
        self.assertIn(
            "published release body is not the changelog section", post_release
        )
        self.assertIn("types: [published]", post_release)
        self.assertIn("schedule:", post_release)
        self.assertIn("releaseReady", post_release)
        publication = (
            PLUGIN_ROOT / ".github" / "workflows" / "publish-release.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("gh release create", publication)
        self.assertIn("gh run download", publication)
        self.assertIn("head_sha", publication)
        self.assertIn("gh attestation verify", publication)
        self.assertNotRegex(publication, r"actions/[a-z-]+@v[0-9]+")

        # ./assets is part of the checkout and carries the plugin's icons and
        # logos, so downloading the evidence into it mixed eighteen tracked
        # images in with the seven release assets, and the exact allowlist was
        # the only thing keeping that artwork off the release. The evidence has
        # to land outside the working tree.
        self.assertTrue(
            (PLUGIN_ROOT / "assets").is_dir(),
            "the tracked assets directory this guard is about is gone",
        )
        self.assertNotIn("--dir assets", publication)
        self.assertNotIn(" assets/*", publication)
        self.assertIn('--dir "${{ runner.temp }}/release-assets"', publication)

        gitignore = (PLUGIN_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("ci/*/node_modules/", gitignore.splitlines())

        installer = (PLUGIN_ROOT / "install.ps1").read_text(encoding="utf-8")
        lifecycle = (
            PLUGIN_ROOT / "scripts" / "run_real_upgrade_rollback.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'Join-Path $PSScriptRoot "scripts/verify_installed.py"', installer
        )
        self.assertIn('Join-Path $root "scripts/verify_installed.py"', lifecycle)
        self.assertNotIn(
            'Join-Path $MarketplaceRoot "scripts/verify_installed.py"', lifecycle
        )
        # The injected fault has to be a verifier that fails after the profile
        # was mutated, so rollback has something to undo. Preflight runs
        # "python -c" before any mutation, so an interpreter that fails
        # unconditionally aborts there and the rollback path is never reached.
        self.assertIn('if `"%1`"==`"-c`" exit /b 0', lifecycle)
        self.assertIn('if [ `"`$1`" = `"-c`" ]; then exit 0; fi', lifecycle)

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
