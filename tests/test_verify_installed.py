from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "verify_installed.py"


def load_module():
    spec = importlib.util.spec_from_file_location("verify_installed", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HostCommandResolutionTests(unittest.TestCase):
    """The host CLI is an npm install, so on Windows it is codex.cmd.

    CreateProcess only ever appends .exe to a name without an extension, so
    handing it a bare "codex" reported an installed and working host as
    unexecutable. Resolution has to go through PATHEXT before the command is
    spawned.
    """

    def test_run_executes_a_shim_that_a_bare_name_would_not_find(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            if os.name == "nt":
                script = directory / "cp-fake-host.cmd"
                script.write_text("@echo {}\r\n", encoding="utf-8")
            else:
                script = directory / "cp-fake-host"
                script.write_text(
                    "#!/bin/sh\necho '{}'\n", encoding="utf-8", newline="\n"
                )
                script.chmod(0o755)
            search = str(directory) + os.pathsep + os.environ["PATH"]
            with mock.patch.dict(os.environ, {"PATH": search}):
                completed = module._run(["cp-fake-host"])
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), "{}")

    def test_unresolvable_command_is_reported_as_an_unexecutable_host(self) -> None:
        module = load_module()
        with mock.patch.object(module.shutil, "which", return_value=None):
            with self.assertRaises(OSError):
                module._run(["cp-absent-host"])
            with self.assertRaisesRegex(RuntimeError, "cannot execute host CLI"):
                module._json_command(module._run, ["cp-absent-host"])


class VerifyInstalledTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        self.installed = self.base / "installed"
        (self.source / ".codex-plugin").mkdir(parents=True)
        (self.source / "skills-core" / "solve-efficiently").mkdir(parents=True)
        (self.source / "skills-core" / "execute-durably").mkdir(parents=True)
        (self.source / "skills-core" / "verify-delivery").mkdir(parents=True)
        (self.source / "skills" / "internal-flow").mkdir(parents=True)
        (self.source / ".gitattributes").write_text("*.txt text\n", encoding="utf-8")
        (self.source / "content.txt").write_text("one\ntwo\n", encoding="utf-8")
        (self.source / ".codex-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "cognitive-powers",
                    "version": "1.6.0",
                    "skills": "./skills-core/",
                }
            ),
            encoding="utf-8",
        )
        for name in ("solve-efficiently", "execute-durably", "verify-delivery"):
            (self.source / "skills-core" / name / "SKILL.md").write_text(
                f"---\nname: {name}\n---\n", encoding="utf-8"
            )
        (self.source / "skills" / "internal-flow" / "SKILL.md").write_text(
            "---\nname: internal-flow\n---\n", encoding="utf-8"
        )
        subprocess.run(["git", "init", "-q"], cwd=self.source, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"],
            cwd=self.source,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"], cwd=self.source, check=True
        )
        subprocess.run(["git", "add", "."], cwd=self.source, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.source, check=True)
        subprocess.run(["git", "tag", "v1.6.0"], cwd=self.source, check=True)
        self.commit = subprocess.run(
            ["git", "rev-parse", "v1.6.0^{commit}"],
            cwd=self.source,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        shutil.copytree(
            self.source, self.installed, ignore=shutil.ignore_patterns(".git")
        )
        (self.installed / "content.txt").write_bytes(b"one\r\ntwo\r\n")
        (self.installed / ".codex-marketplace-install.json").write_text(
            json.dumps(
                {
                    "source_type": "git",
                    "source": "https://github.com/Drizzy07x/cognitive-powers.git",
                    "ref_name": self.commit,
                    "revision": self.commit,
                    "sparse_paths": [],
                }
            ),
            encoding="utf-8",
        )
        self.module = load_module()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def runner(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        if argv == ["codex", "plugin", "marketplace", "list", "--json"]:
            payload = {
                "marketplaces": [
                    {
                        "name": "cognitive-powers",
                        "root": str(self.installed),
                        "marketplaceSource": {
                            "source": "https://github.com/Drizzy07x/cognitive-powers.git"
                        },
                    }
                ]
            }
        elif argv == ["codex", "plugin", "list", "--json"]:
            payload = {
                "installed": [
                    {
                        "name": "cognitive-powers",
                        "pluginId": "cognitive-powers@cognitive-powers",
                        "installed": True,
                        "enabled": True,
                        "version": "1.6.0",
                    }
                ]
            }
        else:
            return subprocess.CompletedProcess(argv, 64, "", "unexpected")
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    def test_checkout_without_git_is_rejected_fail_closed(self) -> None:
        def remove_readonly(function, path, _error) -> None:
            os.chmod(path, stat.S_IWRITE)
            function(path)

        shutil.rmtree(self.source / ".git", onerror=remove_readonly)
        report, code = self.module.verify_installation(
            self.source, self.installed, "v1.6.0", run=self.runner
        )
        self.assertNotEqual(code, 0)
        self.assertFalse(report["matched"])

    def test_exact_install_accepts_git_normalized_crlf_and_only_host_metadata(
        self,
    ) -> None:
        report, code = self.module.verify_installation(
            self.source, self.installed, "v1.6.0", run=self.runner
        )
        self.assertEqual(code, 0, report)
        self.assertTrue(report["matched"])
        self.assertEqual(
            report["content"]["extras"], [".codex-marketplace-install.json"]
        )
        self.assertEqual(
            report["surface"]["exposedSkills"],
            ["execute-durably", "solve-efficiently", "verify-delivery"],
        )
        self.assertEqual(report["surface"]["internalWorkflows"], ["internal-flow"])

    def test_content_change_and_unapproved_extra_use_content_exit_code(self) -> None:
        (self.installed / "content.txt").write_text("tampered\n", encoding="utf-8")
        (self.installed / "extra.txt").write_text("extra", encoding="utf-8")
        report, code = self.module.verify_installation(
            self.source, self.installed, "v1.6.0", run=self.runner
        )
        self.assertEqual(code, self.module.EXIT_CONTENT)
        self.assertFalse(report["content"]["matched"])
        self.assertIn("content.txt", report["content"]["mismatched"])
        self.assertIn("extra.txt", report["content"]["unexpectedExtras"])

    def test_wrong_tag_identity_fails_before_host_cli(self) -> None:
        calls = []

        def forbidden(argv):
            calls.append(argv)
            raise AssertionError("host queried")

        report, code = self.module.verify_installation(
            self.source, self.installed, "v9.9.9", run=forbidden
        )
        self.assertEqual(code, self.module.EXIT_IDENTITY)
        self.assertEqual(calls, [])
        self.assertEqual(report["failureCategory"], "identity")

    def test_duplicate_or_disabled_inventory_fails_closed(self) -> None:
        def duplicate(argv):
            completed = self.runner(argv)
            if argv[1:3] == ["plugin", "list"]:
                payload = json.loads(completed.stdout)
                payload["installed"].append(
                    {**payload["installed"][0], "enabled": False}
                )
                completed.stdout = json.dumps(payload)
            return completed

        report, code = self.module.verify_installation(
            self.source, self.installed, "v1.6.0", run=duplicate
        )
        self.assertEqual(code, self.module.EXIT_INVENTORY)
        self.assertFalse(report["inventory"]["matched"])

    def test_marketplace_must_be_pinned_to_the_resolved_commit_sha(self) -> None:
        metadata_path = self.installed / ".codex-marketplace-install.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["ref_name"] = "v1.6.0"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

        report, code = self.module.verify_installation(
            self.source, self.installed, "v1.6.0", run=self.runner
        )
        self.assertEqual(code, self.module.EXIT_INVENTORY)
        self.assertFalse(report["inventory"]["sourcePinnedToCommit"])

    def test_marketplace_revision_must_equal_the_resolved_commit_sha(self) -> None:
        metadata_path = self.installed / ".codex-marketplace-install.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["revision"] = "0" * 40
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

        report, code = self.module.verify_installation(
            self.source, self.installed, "v1.6.0", run=self.runner
        )
        self.assertEqual(code, self.module.EXIT_INVENTORY)
        self.assertFalse(report["inventory"]["sourcePinnedToCommit"])

    def checkout_installed(self) -> Path:
        """A marketplace root as Codex leaves one: a checkout, no host metadata."""
        target = self.base / "installed-checkout"
        subprocess.run(
            ["git", "clone", "-q", str(self.source), str(target)], check=True
        )
        subprocess.run(["git", "checkout", "-q", self.commit], cwd=target, check=True)
        return target

    def runner_rooted_at(self, root: Path):
        def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
            completed = self.runner(argv)
            if argv == ["codex", "plugin", "marketplace", "list", "--json"]:
                payload = json.loads(completed.stdout)
                payload["marketplaces"][0]["root"] = str(root)
                completed.stdout = json.dumps(payload)
            return completed

        return run

    def test_checkout_at_the_release_commit_needs_no_host_metadata(self) -> None:
        # Codex does not leave .codex-marketplace-install.json where it roots a
        # marketplace, so demanding it refused installations that are in fact at
        # the release commit. The checkout answers that question directly.
        installed = self.checkout_installed()
        report, code = self.module.verify_installation(
            self.source, installed, "v1.6.0", run=self.runner_rooted_at(installed)
        )
        self.assertEqual(code, 0, report)
        self.assertTrue(report["inventory"]["revisionPinnedToCommit"])
        self.assertTrue(report["inventory"]["sourcePinnedToCommit"])
        self.assertFalse(report["inventory"]["installMetadataPresent"])

    def test_host_metadata_that_disagrees_refuses_a_matching_checkout(self) -> None:
        installed = self.checkout_installed()
        (installed / ".codex-marketplace-install.json").write_text(
            json.dumps(
                {
                    "source_type": "git",
                    "source": "https://github.com/Drizzy07x/cognitive-powers.git",
                    "ref_name": self.commit,
                    "revision": "0" * 40,
                    "sparse_paths": [],
                }
            ),
            encoding="utf-8",
        )
        report, code = self.module.verify_installation(
            self.source, installed, "v1.6.0", run=self.runner_rooted_at(installed)
        )
        self.assertEqual(code, self.module.EXIT_INVENTORY)
        self.assertTrue(report["inventory"]["revisionPinnedToCommit"])
        self.assertFalse(report["inventory"]["sourcePinnedToCommit"])

    def test_neither_a_checkout_nor_host_metadata_fails_closed(self) -> None:
        (self.installed / ".codex-marketplace-install.json").unlink()
        report, code = self.module.verify_installation(
            self.source, self.installed, "v1.6.0", run=self.runner
        )
        self.assertEqual(code, self.module.EXIT_INVENTORY)
        self.assertFalse(report["inventory"]["revisionPinnedToCommit"])
        self.assertFalse(report["inventory"]["sourcePinnedToCommit"])

    def test_marketplace_root_must_equal_the_verified_installed_root(self) -> None:
        other = self.base / "other-marketplace"
        other.mkdir()

        def wrong_root(argv):
            completed = self.runner(argv)
            if argv == ["codex", "plugin", "marketplace", "list", "--json"]:
                payload = json.loads(completed.stdout)
                payload["marketplaces"][0]["root"] = str(other)
                completed.stdout = json.dumps(payload)
            return completed

        report, code = self.module.verify_installation(
            self.source, self.installed, "v1.6.0", run=wrong_root
        )
        self.assertEqual(code, self.module.EXIT_INVENTORY)
        self.assertFalse(report["inventory"]["marketplaceRootMatchesInstalledRoot"])
        self.assertEqual(report["inventory"]["marketplaceRoot"], str(other.resolve()))

    def test_install_script_runs_canonical_verifier_as_a_postcondition(self) -> None:
        script = (ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("scripts/verify_installed.py", script)
        self.assertIn("--installed-root", script)
        self.assertIn("$installedMarketplaceRoot", script)


if __name__ == "__main__":
    unittest.main()
