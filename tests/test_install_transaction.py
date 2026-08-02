from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.ps1"
INSTALLER_SH = ROOT / "install.sh"
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


class InstallTransactionScenarios:
    """Every scenario both installers owe, written once.

    install.sh is a port, and a port is only worth having if it is held to the
    same contract. Two files of near-identical scenarios would drift the moment
    one of them gained a case, and the drift would look like coverage. The
    concrete classes below supply only what genuinely differs: how the script is
    invoked, how a shim is spelled on this platform, and where the host puts the
    recovery directory.
    """

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
        self.write_python_shim()

    def write_python_shim(self, exit_code: int | None = None) -> None:
        """Stand in for the interpreter the installer runs.

        install.ps1 uses it twice, for the version preflight and the final
        verifier, so a flat `exit 0` isolates the verifier without breaking
        anything. install.sh also parses every JSON document with it, so the
        same flat shim would make the port fail for a reason the PowerShell path
        cannot have; the bash class overrides this to keep a real interpreter and
        stub only the verifier.
        """
        if exit_code is None:
            exit_code = 0
        self.shim(
            "python",
            windows=f"@exit /b {exit_code}\r\n",
            posix=f"#!/bin/sh\nexit {exit_code}\n",
        )

    def resolve_on_path(self, name: str, search: str) -> str | None:
        return shutil.which(name, path=search)

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
            self.write_python_shim(python_exit)
        env = self.installer_environment()
        return subprocess.run(
            self.installer_argv(),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            # pwsh cold start plus per-call .cmd shims can eat several seconds
            # on a loaded Windows runner; 30s was the tightest wall-clock
            # budget in the suite and its failure mode carries no installer
            # output at all.
            timeout=120,
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
        env.update(self.extra_environment())
        return env

    def extra_environment(self) -> dict[str, str]:
        return {}

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
            resolved = self.resolve_on_path(name, search)
            with self.subTest(command=name):
                self.assertIsNotNone(resolved, f"{name} resolves to nothing")
                self.assert_inside_fixture_bin(name, resolved)

    def assert_inside_fixture_bin(self, name: str, resolved: str) -> None:
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

    def test_preflight_accepts_the_previous_rollback_marketplace(self) -> None:
        if self.recovery_scenario_skip:
            self.skipTest(self.recovery_scenario_skip)
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
        self.assertEqual(recovery[0]["root"], self.previous_root.resolve().as_posix())
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


# The installer is a PowerShell 7 script. CI runners ship pwsh, so its absence
# was reported as ten broken tests rather than an unexercised suite; a machine
# without it must say so once, and say what it did not cover.
@unittest.skipUnless(
    shutil.which("pwsh") is not None,
    "PowerShell 7 (pwsh) is not installed; install.ps1 cannot be exercised",
)
class InstallTransactionTests(InstallTransactionScenarios, unittest.TestCase):
    """The PowerShell installer.

    The name is load-bearing: scripts/run_compatibility_scenarios.py names two
    of these tests by their fully qualified path to bind the corrupt-state and
    legacy-copy cells, so renaming the class silently empties those scenarios.
    """

    recovery_scenario_skip = ""

    def installer_argv(self) -> list[str]:
        return [
            "pwsh",
            "-NoProfile",
            "-File",
            str(INSTALLER),
            "-ReleaseRef",
            "v1.6.0",
        ]

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


@unittest.skipUnless(
    shutil.which("bash") is not None,
    "bash is not installed; install.sh cannot be exercised",
)
class InstallShTransactionTests(InstallTransactionScenarios, unittest.TestCase):
    """The POSIX installer, held to the same scenarios.

    Every shim is extensionless and carries a shebang, on every platform: bash
    resolves PATH the POSIX way and never consults PATHEXT, so a .cmd shim is
    invisible to it and the runner's own git and gh would answer instead. That
    is the failure this suite already learned once on the PowerShell side, where
    Windows-only .cmd shims left Linux and macOS measuring the real binaries.
    """

    def setUp(self) -> None:
        super().setUp()
        # install.sh reads XDG_DATA_HOME first, which is the same directory
        # .NET reports as LocalApplicationData on Unix. Setting it exercises the
        # platform's own rule instead of a guess, and keeps the recovery
        # directory inside the fixture rather than in the developer's profile.
        self.xdg_data_home = self.base / "xdg"
        self.xdg_data_home.mkdir(exist_ok=True)

    @property
    def recovery_scenario_skip(self) -> str:
        if os.name != "nt":
            return ""
        # install.sh recognizes a recovery marketplace only at an absolute POSIX
        # path, which is the only kind that exists on the hosts it targets. The
        # fixture's roots are drive-letter paths here, so this one scenario
        # would be asserting against a path shape the script is right to refuse.
        # Everything before and after it in the transaction still runs.
        return (
            "install.sh recognizes recovery marketplaces by absolute POSIX path; "
            "the Windows fixture supplies drive-letter paths"
        )

    def installer_argv(self) -> list[str]:
        return ["bash", INSTALLER_SH.as_posix(), "--release-ref", "v1.6.0"]

    def shim(self, name: str, *, windows: str, posix: str) -> None:
        path = self.bin / name
        path.write_text(posix, encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def write_python_shim(self, exit_code: int | None = None) -> None:
        # install.sh parses every JSON document with this interpreter, so it
        # cannot be replaced wholesale the way the PowerShell harness replaces
        # it. Only the canonical verifier is stubbed -- the boundary this suite
        # means to isolate -- and both PEP 394 names are written, because the
        # script prefers python3 and falls back to python.
        for name in ("python", "python3"):
            if exit_code is None:
                body = (
                    "#!/bin/sh\n"
                    'case "${1:-}" in\n'
                    "  *verify_installed.py) exit 0 ;;\n"
                    "esac\n"
                    f'exec "{Path(sys.executable).as_posix()}" "$@"\n'
                )
            else:
                body = f"#!/bin/sh\nexit {exit_code}\n"
            path = self.bin / name
            path.write_text(body, encoding="utf-8", newline="\n")
            path.chmod(0o755)

    def resolve_on_path(self, name: str, search: str) -> str | None:
        # shutil.which applies PATHEXT on Windows and so cannot see an
        # extensionless shim; bash can, and bash is what resolves these names
        # when the installer runs. Ask the shell that will actually do it.
        completed = subprocess.run(
            ["bash", "-c", 'command -v "$1"', "bash", name],
            env={**os.environ, "PATH": search},
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return None
        return completed.stdout.strip() or None

    def assert_inside_fixture_bin(self, name: str, resolved: str) -> None:
        # bash answers in its own path vocabulary, which on Windows is a POSIX
        # spelling of a drive-letter path. Comparing that to a WindowsPath
        # compares two notations, not two locations, so let bash canonicalize
        # the fixture directory too and compare like with like.
        completed = subprocess.run(
            ["bash", "-c", 'cd "$1" && pwd -P', "bash", self.bin.as_posix()],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(
            str(PurePosixPath(resolved).parent),
            completed.stdout.strip(),
            f"{name} resolves to {resolved}, outside the fixture",
        )

    def extra_environment(self) -> dict[str, str]:
        return {"XDG_DATA_HOME": self.xdg_data_home.as_posix()}

    def local_application_data(self) -> Path:
        return self.xdg_data_home

    def test_release_ref_pattern_is_enforced_before_anything_runs(self) -> None:
        """PowerShell rejects a malformed ref at parameter binding; sh cannot.

        [ValidatePattern] means install.ps1 never reaches its body with a ref
        that is not vX.Y.Z. install.sh has to apply the same rule itself, and it
        has to apply it before the preflight, because $expected_version is a
        substring of this value: a ref that is not a tag would be verified
        against whatever the substring happened to be.
        """
        self.state()
        for candidate in ("1.6.0", "v1.6", "main", "v1.6.0; rm -rf /"):
            with self.subTest(release_ref=candidate):
                result = subprocess.run(
                    ["bash", INSTALLER_SH.as_posix(), "--release-ref", candidate],
                    env=self.installer_environment(),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=120,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.read_state()["log"], [])

    def test_both_installers_declare_the_same_default_release_ref(self) -> None:
        """A second installer is a second place for the tag to go stale.

        bump_version.py moves both, and this is the assertion that fails if a
        future carrier is added to one and not the other -- the failure mode the
        1.7.1-era stale-tag defects all shared.
        """
        powershell = INSTALLER.read_text(encoding="utf-8")
        posix = INSTALLER_SH.read_text(encoding="utf-8")
        declared = re.search(r'\[string\]\$ReleaseRef = "(v[\d.]+)"', powershell)
        ported = re.search(r'^release_ref="(v[\d.]+)"$', posix, re.MULTILINE)
        self.assertIsNotNone(declared)
        self.assertIsNotNone(ported)
        self.assertEqual(declared.group(1), ported.group(1))


if __name__ == "__main__":
    unittest.main()
