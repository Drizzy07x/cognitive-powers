from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PLUGIN_ROOT / "scripts" / "doctor.py"
SPEC = importlib.util.spec_from_file_location("doctor", MODULE_PATH)
doctor = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(doctor)


def make_installable_fixture(parent: Path) -> Path:
    root = parent / "plugin"
    (root / ".codex-plugin").mkdir(parents=True)
    (root / "skills" / "focused").mkdir(parents=True)
    (root / "skills" / "solve-efficiently" / "scripts").mkdir(parents=True)
    (root / "hooks").mkdir()
    (root / "scripts").mkdir()
    (root / "benchmarks").mkdir()
    (root / "benchmarks" / "confirmatory").mkdir()
    (root / "integrations").mkdir()
    (root / "assets").mkdir()
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "fixture",
                "version": "1.0.0",
                "skills": "./skills/",
                "hooks": "./hooks/hooks.json",
                "interface": {
                    "composerIcon": "./assets/composer-icon.svg",
                    "logo": "./assets/plugin-logo-light.png",
                    "logoDark": "./assets/plugin-logo-dark.png",
                    "screenshots": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "skills" / "focused" / "SKILL.md").write_text(
        "---\nname: focused\ndescription: Use for fixture checks.\n---\n",
        encoding="utf-8",
    )
    (root / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": {"Stop": []}}), encoding="utf-8"
    )
    (root / "hooks" / "selective_hooks.py").write_text("", encoding="utf-8")
    (root / "scripts" / "doctor.py").write_text(
        MODULE_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    canonical_orchestration = PLUGIN_ROOT / "scripts" / "orchestration_policy.py"
    wrapper_orchestration = (
        PLUGIN_ROOT
        / "skills"
        / "solve-efficiently"
        / "scripts"
        / "orchestration_policy.py"
    )
    (root / "scripts" / "orchestration_policy.py").write_text(
        canonical_orchestration.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (
        root / "skills" / "solve-efficiently" / "scripts" / "orchestration_policy.py"
    ).write_text(wrapper_orchestration.read_text(encoding="utf-8"), encoding="utf-8")
    (root / "scripts" / "validate_all.py").write_text("", encoding="utf-8")
    (root / "scripts" / "release_witness.py").write_text("", encoding="utf-8")
    for name in (
        "controller_ab_fixtures.py",
        "controller_ab_batch.py",
        "prepare_controller_ab_homes.py",
    ):
        (root / "scripts" / name).write_text("", encoding="utf-8")
    (root / "benchmarks" / "controller_ab_protocol.json").write_text(
        json.dumps({"schema_version": 1, "claim_status": "not-proven"}),
        encoding="utf-8",
    )
    (root / "benchmarks" / "confirmatory" / "controller_ab_corpus.json").write_text(
        json.dumps({"schema_version": 1}), encoding="utf-8"
    )
    (root / ".github" / "workflows" / "validate.yml").write_text(
        "name: Validate\n", encoding="utf-8"
    )
    for name in (
        "composer-icon.svg",
        "plugin-logo-light.png",
        "plugin-logo-dark.png",
    ):
        (root / "assets" / name).write_text("asset", encoding="utf-8")
    (root / "integrations" / "catalog.json").write_text(
        json.dumps({"sources": []}), encoding="utf-8"
    )
    return root


class DoctorTests(unittest.TestCase):
    def test_current_checkout_report_is_read_only_and_truthful(self) -> None:
        report = doctor.build_report(PLUGIN_ROOT)
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertTrue(report["readOnly"])
        self.assertEqual(report["plugin"]["name"], manifest["name"])
        self.assertEqual(report["plugin"]["version"], manifest["version"])
        self.assertEqual(report["skills"]["count"], len(report["skills"]["names"]))
        self.assertEqual(report["skills"]["count"], 3)
        self.assertGreaterEqual(report["skills"]["internalCount"], 14)
        self.assertTrue(report["hooks"]["available"])
        self.assertTrue(report["git"]["available"])
        self.assertEqual(len(report["source"]["sha256"]), 64)
        self.assertTrue(report["validation"]["available"])
        self.assertFalse(report["validation"]["liveValidated"])

    def test_provider_inventory_does_not_probe_subprocess_or_network(self) -> None:
        with mock.patch.object(
            doctor.subprocess, "run", side_effect=AssertionError("probe attempted")
        ):
            providers = doctor.provider_declarations(PLUGIN_ROOT)
        self.assertFalse(providers["networkProbed"])
        self.assertFalse(providers["executablesProbed"])
        self.assertFalse(providers["installationAttempted"])
        self.assertTrue(providers["availabilityUnknown"])
        self.assertTrue(providers["declared"])

    def test_usage_counters_explicitly_abstain_without_a_privacy_safe_seam(
        self,
    ) -> None:
        policy = doctor.build_report(PLUGIN_ROOT)["localUsageCounters"]

        self.assertEqual(policy["status"], "abstained")
        self.assertEqual(policy["reasonCode"], "no-privacy-safe-natural-seam")
        self.assertFalse(policy["implemented"])
        self.assertFalse(policy["writesLocalState"])
        self.assertEqual(
            policy["prohibitedFields"],
            ["prompts", "commands", "outputs", "paths", "identifiers"],
        )
        self.assertEqual(policy["controls"], [])

    def test_source_identity_changes_when_packaged_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.txt"
            source.write_text("first", encoding="utf-8")
            before = doctor.source_identity(root)
            (root / ".codex-marketplace-install.json").write_text(
                json.dumps({"installed": True}), encoding="utf-8"
            )
            after_host_metadata = doctor.source_identity(root)
            source.write_text("second", encoding="utf-8")
            after = doctor.source_identity(root)
        self.assertEqual(before, after_host_metadata)
        self.assertNotEqual(before["sha256"], after["sha256"])

    def test_git_identity_ignores_only_codex_marketplace_metadata(self) -> None:
        completed = doctor.subprocess.CompletedProcess
        with mock.patch.object(
            doctor.subprocess,
            "run",
            side_effect=[
                completed(["git"], 0, stdout="a" * 40 + "\n", stderr=""),
                completed(
                    ["git"],
                    0,
                    stdout=(
                        "?? .codex-marketplace-install.json\n M tracked-source.py\n"
                    ),
                    stderr="",
                ),
            ],
        ) as run:
            identity = doctor.git_identity(Path("plugin"))

        self.assertEqual(run.call_args_list[1].args[0][-2:], ["--", "."])
        self.assertTrue(identity["dirty"])

        self.assertEqual(
            identity["ignoredHostEntries"],
            ["?? .codex-marketplace-install.json"],
        )

    def test_release_installation_uses_disposable_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = make_installable_fixture(Path(temporary))
            result = doctor.validate_release_installation(root)
        self.assertTrue(result["passed"])
        self.assertTrue(result["temporaryCopy"])
        self.assertFalse(result["published"])
        self.assertGreater(result["fileCount"], 0)
        self.assertTrue(all(check["passed"] for check in result["checks"]))

    def test_release_installation_fails_when_package_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = make_installable_fixture(Path(temporary))
            (root / "hooks" / "hooks.json").unlink()
            result = doctor.validate_release_installation(root)
        self.assertFalse(result["passed"])
        self.assertTrue(
            any(
                check["name"] == "hooks" and not check["passed"]
                for check in result["checks"]
            )
        )

    def test_release_installation_rejects_missing_runtime_assets_and_gates(
        self,
    ) -> None:
        cases = {
            "hook-script": ("hooks/selective_hooks.py", "hook-script"),
            "composer-icon": (
                "assets/composer-icon.svg",
                "interface-asset:composerIcon",
            ),
            "light-logo": (
                "assets/plugin-logo-light.png",
                "interface-asset:logo",
            ),
            "release-witness": ("scripts/release_witness.py", "release-witness"),
            "ci-workflow": (".github/workflows/validate.yml", "ci-workflow"),
            "orchestration-runtime": (
                "scripts/orchestration_policy.py",
                "orchestration-runtime",
            ),
            "orchestration-wrapper": (
                "skills/solve-efficiently/scripts/orchestration_policy.py",
                "orchestration-wrapper",
            ),
            "controller-ab-protocol": (
                "benchmarks/controller_ab_protocol.json",
                "controller-ab-protocol",
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            for label, (relative, expected_check) in cases.items():
                with self.subTest(label=label):
                    root = make_installable_fixture(parent / label)
                    (root / relative).unlink()
                    result = doctor.validate_release_installation(root)
                    self.assertFalse(result["passed"])
                    self.assertTrue(
                        any(
                            check["name"] == expected_check and not check["passed"]
                            for check in result["checks"]
                        )
                    )

    def test_staged_doctor_result_must_confirm_runtime_components(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = make_installable_fixture(Path(temporary))
            (root / "hooks" / "selective_hooks.py").unlink()
            result = doctor.validate_release_installation(root)
        self.assertTrue(
            any(
                check["name"] == "doctor-execution" and not check["passed"]
                for check in result["checks"]
            )
        )


if __name__ == "__main__":
    unittest.main()
