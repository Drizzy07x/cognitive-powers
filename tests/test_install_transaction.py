from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.ps1"
FAKE = ROOT / "tests" / "fixtures" / "fake_codex_cli.py"
FAKE_GH = ROOT / "tests" / "fixtures" / "fake_gh_cli.py"


class InstallerProfileLocationTests(unittest.TestCase):
    """Locating the rollback copy must not depend on a folder Unix may lack.

    GetFolderPath verifies the directory on Unix and returns an empty string
    when it is missing, and a profile that has never been written to has no
    ~/.local/share. Join-Path then refuses the empty string, so the installer
    died before it could prepare any recovery -- which is what every Unix cell
    hit once the disposable Codex home let it get that far.
    """

    def test_local_application_data_is_created_rather_than_verified(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn(
            '[Environment]::GetFolderPath("LocalApplicationData", "Create")', source
        )
        self.assertNotIn('GetFolderPath("LocalApplicationData")', source)


# The installer is a PowerShell 7 script. CI runners ship pwsh, so its absence
# was reported as ten broken tests rather than an unexercised suite; a machine
# without it must say so once, and say what it did not cover.
@unittest.skipUnless(
    shutil.which("pwsh") is not None,
    "PowerShell 7 (pwsh) is not installed; install.ps1 cannot be exercised",
)
class InstallTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.release_root = self.base / "release"
        (self.release_root / ".agents" / "plugins").mkdir(parents=True)
        (self.release_root / ".codex-plugin").mkdir()
        (self.release_root / ".agents" / "plugins" / "marketplace.json").write_text(
            "{}", encoding="utf-8"
        )
        (self.release_root / ".codex-plugin" / "plugin.json").write_text(
            json.dumps({"version": "1.6.0"}), encoding="utf-8"
        )
        self.previous_root = self.base / "previous"
        (self.previous_root / ".agents" / "plugins").mkdir(parents=True)
        (self.previous_root / ".codex-plugin").mkdir()
        (self.previous_root / ".agents" / "plugins" / "marketplace.json").write_text(
            "{}", encoding="utf-8"
        )
        (self.previous_root / ".codex-plugin" / "plugin.json").write_text(
            json.dumps({"version": "1.5.2"}), encoding="utf-8"
        )
        self.personal_root = self.base / "personal"
        self.personal_root.mkdir()
        self.state_path = self.base / "state.json"
        python = Path(sys.executable)
        # Resolved before the shim exists, so it names the runner's real Git.
        self.real_git = shutil.which("git") or "git"
        self.shim(
            "codex",
            windows=f'@"{python}" "{FAKE}" %*\r\n',
            posix=f'#!/bin/sh\nexec "{python}" "{FAKE}" "$@"\n',
        )
        self.shim(
            "gh",
            windows=f'@"{python}" "{FAKE_GH}" %*\r\n',
            posix=f'#!/bin/sh\nexec "{python}" "{FAKE_GH}" "$@"\n',
        )
        self.shim(
            "git",
            windows=(
                f'@if "%1"=="-C" echo {"b" * 40}\r\n'
                '@if "%1"=="-C" exit /b 0\r\n'
                "@git.exe %*\r\n"
            ),
            posix=(
                "#!/bin/sh\n"
                f'if [ "$1" = "-C" ]; then echo {"b" * 40}; exit 0; fi\n'
                f'exec "{self.real_git}" "$@"\n'
            ),
        )
        # The transaction harness exercises rollback and ordering.  The canonical
        # verifier has its own real-Git fixture tests, so isolate this boundary.
        self.shim("python", windows="@exit /b 0\r\n", posix="#!/bin/sh\nexit 0\n")

    def shim(self, name: str, *, windows: str, posix: str) -> None:
        """Write one fake CLI that really shadows the real one on this platform.

        A ``.cmd`` file is inert outside Windows: PATH lookup skips it, the
        runner's own binary answers instead, and the installer is then measured
        against real Git and an unauthenticated GitHub CLI rather than against
        the fixture. That does not weaken the assertions, it detaches them from
        their subject, so every claim about what the transaction changed fails
        for a reason that has nothing to do with the transaction.
        """
        if os.name == "nt":
            (self.bin / f"{name}.cmd").write_text(windows, encoding="utf-8")
            return
        path = self.bin / name
        path.write_text(posix, encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def state(self, *, installed=None, marketplaces=None, failures=None) -> dict:
        payload = {
            "release_root": str(self.release_root),
            "previous_root": str(self.previous_root),
            "release_commit": "a" * 40,
            "previous_commit": "b" * 40,
            "target_version": "1.6.0",
            "personal_version": "1.5.2",
            "installed": installed or [],
            "marketplaces": marketplaces or [],
            "failures": failures or {},
            "log": [],
        }
        self.state_path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def marketplace(self, source="Drizzy07x/cognitive-powers@v1.5.2") -> dict:
        return {
            "name": "cognitive-powers",
            "root": str(self.previous_root),
            "marketplaceSource": {"source": source},
        }

    def plugin(
        self, plugin_id="cognitive-powers@cognitive-powers", version="1.5.2"
    ) -> dict:
        return {
            "name": "cognitive-powers",
            "pluginId": plugin_id,
            "installed": True,
            "enabled": True,
            "version": version,
        }

    def run_installer(
        self, *, gh_exit=0, python_exit=None
    ) -> subprocess.CompletedProcess[str]:
        if gh_exit:
            self.shim(
                "gh",
                windows=f"@exit /b {gh_exit}\r\n",
                posix=f"#!/bin/sh\nexit {gh_exit}\n",
            )
        if python_exit is not None:
            self.shim(
                "python",
                windows=f"@exit /b {python_exit}\r\n",
                posix=f"#!/bin/sh\nexit {python_exit}\n",
            )
        env = self.installer_environment()
        return subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(INSTALLER), "-ReleaseRef", "v1.6.0"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def installer_environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": str(self.bin) + os.pathsep + env["PATH"],
                "FAKE_CODEX_STATE": str(self.state_path),
                "LOCALAPPDATA": str(self.base / "localappdata"),
                "HOME": str(self.base / "home"),
                "USERPROFILE": str(self.base / "home"),
                "CODEX_HOME": str(self.base / "codex-home"),
            }
        )
        return env

    def read_state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def test_every_fake_cli_actually_shadows_the_real_one(self) -> None:
        """Assertions about the transaction only mean something if PATH obeys.

        Windows-only ``.cmd`` shims left this suite measuring the runner's own
        Git and an unauthenticated GitHub CLI on Linux and macOS, where the
        installer aborts at its first real call and no assertion about the
        transaction can hold. Resolve each command the way the installer will.
        """
        search = str(self.bin) + os.pathsep + os.environ["PATH"]
        for name in ("codex", "gh", "git", "python"):
            resolved = shutil.which(name, path=search)
            with self.subTest(command=name):
                self.assertIsNotNone(resolved, f"{name} resolves to nothing")
                self.assertEqual(
                    Path(resolved).parent,
                    self.bin,
                    f"{name} resolves to {resolved}, outside the fixture",
                )

    def test_tag_preflight_fails_before_profile_query_or_mutation(self) -> None:
        self.state()
        result = self.run_installer(gh_exit=7)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.read_state()["log"], [])

    def test_unusable_python_fails_before_profile_query_or_mutation(self) -> None:
        # 9009 is what the Microsoft Store alias at WindowsApps\python.exe
        # returns: the name resolves, so a resolution-only check passes it
        # through and the interpreter is not missed until the final verifier,
        # after the profile has been mutated. 3 is a real interpreter below the
        # supported minimum. POSIX masks an exit code to 8 bits, so 9009 arrives
        # as 49 there; only its being non-zero is load-bearing.
        for python_exit in (9009, 3):
            with self.subTest(python_exit=python_exit):
                self.state()
                result = self.run_installer(python_exit=python_exit)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.read_state()["log"], [])

    def test_untrusted_marketplace_sources_fail_closed_without_mutation(self) -> None:
        for source in (
            None,
            "",
            "https://evilgithub.com/Drizzy07x/cognitive-powers",
            "other/repository",
        ):
            with self.subTest(source=source):
                marketplace = self.marketplace(source or "")
                self.state(marketplaces=[marketplace])
                result = self.run_installer()
                state = self.read_state()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(
                    any(
                        args[:3] == ["plugin", "marketplace", "remove"]
                        for args in state["log"]
                    )
                )

    def local_application_data(self) -> Path:
        # install.ps1 locates recovery marketplaces through GetFolderPath.
        # Where that lands differs by platform and .NET version, and modeling
        # it here guessed wrong twice -- so ask the same pwsh, under the same
        # profile overrides run_installer uses, and let the platform answer.
        completed = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-Command",
                '[Environment]::GetFolderPath("LocalApplicationData", "Create")',
            ],
            env=self.installer_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        return Path(completed.stdout.strip())

    def test_preflight_accepts_the_previous_rollback_marketplace(self) -> None:
        # A failed transaction restores from a recovery marketplace under
        # LocalApplicationData and preserves it. That state is the installer's
        # own product, so the next run must proceed and re-point it at the new
        # immutable SHA instead of refusing the recovery it created.
        recovery = (
            self.local_application_data()
            / "cognitive-powers"
            / "rollback-3f2b6c1a-9d4e-4f88-b1c2-7a5d9e0f1234"
            / "marketplace"
        )
        (recovery / ".agents" / "plugins").mkdir(parents=True, exist_ok=True)
        (recovery / ".codex-plugin").mkdir(parents=True, exist_ok=True)
        (recovery / ".agents" / "plugins" / "marketplace.json").write_text(
            "{}", encoding="utf-8"
        )
        (recovery / ".codex-plugin" / "plugin.json").write_text(
            json.dumps({"version": "1.5.2"}), encoding="utf-8"
        )
        self.state(
            installed=[self.plugin()],
            marketplaces=[
                {
                    "name": "cognitive-powers",
                    "root": str(recovery),
                    "marketplaceSource": {"source": str(recovery)},
                }
            ],
        )

        result = self.run_installer()

        state = self.read_state()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(
            any(
                args[:4]
                == ["plugin", "marketplace", "add", "Drizzy07x/cognitive-powers"]
                for args in state["log"]
            ),
            f"the recovery marketplace was never re-pointed: {state['log']}",
        )

    def test_orphan_private_plugin_fails_before_removals(self) -> None:
        self.state(installed=[self.plugin()])
        result = self.run_installer()
        state = self.read_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(args[:2] == ["plugin", "remove"] for args in state["log"]))

    def test_private_and_personal_state_is_restored_after_provisional_failure(
        self,
    ) -> None:
        prior = [self.plugin(), self.plugin("cognitive-powers@personal", "1.5.2")]
        self.state(
            installed=prior,
            marketplaces=[
                self.marketplace(),
                {
                    "name": "personal",
                    "root": str(self.personal_root),
                    "marketplaceSource": {"source": "local"},
                },
            ],
            failures={"plugin add cognitive-powers@cognitive-powers --json": 1},
        )
        result = self.run_installer()
        state = self.read_state()
        self.assertNotEqual(result.returncode, 0)
        restored = {
            (p["pluginId"], p["version"], p["enabled"]) for p in state["installed"]
        }
        self.assertEqual(
            restored, {(p["pluginId"], p["version"], p["enabled"]) for p in prior}
        )
        recovery = [m for m in state["marketplaces"] if m["name"] == "cognitive-powers"]
        self.assertEqual(len(recovery), 1)
        self.assertEqual(recovery[0]["root"], str(self.previous_root.resolve()))
        self.assertEqual(
            recovery[0]["marketplaceSource"]["source"],
            "Drizzy07x/cognitive-powers@" + "b" * 40,
        )

    def test_personal_only_is_restored_after_failure(self) -> None:
        personal = self.plugin("cognitive-powers@personal", "1.5.2")
        self.state(
            installed=[personal],
            marketplaces=[
                {
                    "name": "personal",
                    "root": str(self.personal_root),
                    "marketplaceSource": {"source": "local"},
                }
            ],
            failures={"plugin add cognitive-powers@cognitive-powers --json": 1},
        )
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.read_state()["installed"], [personal])

    def test_cleanup_failure_cannot_report_success_and_preserves_recovery(self) -> None:
        self.state(
            installed=[self.plugin()],
            marketplaces=[self.marketplace()],
            failures={"plugin marketplace remove cognitive-powers": 2},
        )
        result = self.run_installer()
        state = self.read_state()
        self.assertNotEqual(result.returncode, 0)
        recovery = [m for m in state["marketplaces"] if m["name"] == "cognitive-powers"]
        self.assertTrue(recovery)
        self.assertTrue(any("rollback-" in m["root"] for m in recovery))

    def test_success_has_one_enabled_private_target_version(self) -> None:
        self.state()
        result = self.run_installer()
        state = self.read_state()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        matches = [
            p
            for p in state["installed"]
            if p["name"] == "cognitive-powers" and p["installed"]
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(
            (matches[0]["pluginId"], matches[0]["version"], matches[0]["enabled"]),
            ("cognitive-powers@cognitive-powers", "1.6.0", True),
        )
        marketplace_add = next(
            args
            for args in state["log"]
            if args[:3] == ["plugin", "marketplace", "add"]
            and args[3] == "Drizzy07x/cognitive-powers"
        )
        self.assertEqual(marketplace_add[marketplace_add.index("--ref") + 1], "a" * 40)


if __name__ == "__main__":
    unittest.main()
