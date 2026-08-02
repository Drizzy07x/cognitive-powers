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

    # A scenario a concrete installer cannot express states why here, so that
    # skipping is a decision on the record rather than a silently absent case.
    recovery_scenario_skip = ""

    # Declared rather than left to AttributeError: a third installer class is
    # the obvious next use of this mixin, and "object has no attribute" names
    # neither the hook nor the reason it exists.
    def installer_path(self) -> Path:
        raise NotImplementedError("name the installer script this class exercises")

    def installer_argv(self, script: Path | None = None) -> list[str]:
        raise NotImplementedError("give the argv that runs this installer")

    def local_application_data(self) -> Path:
        raise NotImplementedError("name the directory this host puts recovery in")

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
        self.write_git_shim("b" * 40)
        # The transaction harness exercises rollback and ordering.  The canonical
        # verifier has its own real-Git fixture tests, so isolate this boundary.
        self.write_python_shim()

    def write_git_shim(self, revision: str) -> None:
        """Answer `git -C <root> rev-parse HEAD` with a revision a test chooses.

        Both installers accept a restore from the pinned remote only when the
        restored marketplace really sits on the previous commit, and a fixed
        answer of exactly that commit makes the comparison unfalsifiable: the
        branch that rejects a marketplace restored to the wrong revision was
        reachable only if the shim could disagree.
        """
        self.shim(
            "git",
            windows=(
                f'@if "%1"=="-C" echo {revision}\r\n'
                '@if "%1"=="-C" exit /b 0\r\n'
                "@git.exe %*\r\n"
            ),
            posix=(
                "#!/bin/sh\n"
                f'if [ "$1" = "-C" ]; then echo {revision}; exit 0; fi\n'
                f'exec "{self.real_git}" "$@"\n'
            ),
        )

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
        self, *, gh_exit=0, python_exit=None, script=None
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
            self.installer_argv(script),
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

    def test_missing_verifier_fails_before_profile_query_or_mutation(self) -> None:
        """The postcondition's own file is a preflight fact, not a late surprise.

        Both installers resolve scripts/verify_installed.py beside themselves, so
        a copy moved away from its checkout has no postcondition to run. Left
        until the end, that is discovered only after the profile was mutated and
        is reported as a failed installation and a rollback -- which is what the
        README's PowerShell one-liner produced for every user who ran it, since a
        scriptblock has no script path and fetching install.ps1 alone never
        brought the verifier along. Its absence is knowable before anything is
        touched, so nothing may be touched before it is checked.
        """
        stray = self.base / "stray"
        stray.mkdir()
        copy = stray / self.installer_path().name
        shutil.copy2(self.installer_path(), copy)
        self.state()

        result = self.run_installer(script=copy)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.read_state()["log"], [])
        self.assertIn("verif", (result.stdout + result.stderr).casefold())

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

    def recovery_directories(self) -> list[Path]:
        """Recovery copies that are still on disk after the installer exited."""
        parent = self.local_application_data() / "cognitive-powers"
        if not parent.is_dir():
            return []
        return sorted(path for path in parent.glob("rollback-*") if path.is_dir())

    def flattened_output(self, result: subprocess.CompletedProcess[str]) -> str:
        """Both streams as one line, with PowerShell's error framing removed.

        A thrown PowerShell error is colourized and word-wrapped to the console
        width, with every continuation line prefixed by "  | ", so a sentence in
        it matches no phrase and a path can end up on a line of its own. The
        wrap is at spaces, so rejoining the lines with a space restores the
        text; the shell's own message arrives as one line and is unaffected.
        """
        text = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout + "\n" + result.stderr)
        lines = (re.sub(r"^\s*\|\s?", "", line).strip() for line in text.splitlines())
        return " ".join(line for line in lines if line)

    def assert_named_recovery_exists(
        self, result: subprocess.CompletedProcess[str]
    ) -> None:
        """A message naming a directory has to name one that is there.

        The failure text tells the operator to keep a recovery marketplace, and
        the cleanup step removes it unless the transaction asked otherwise.
        Nothing checked that those two agreed, so the message was free to name a
        path that had already been deleted -- advice that reads as reassurance
        and leaves nothing behind to act on.
        """
        output = self.flattened_output(result)
        self.assertRegex(
            output,
            r"[Rr]ecovery marketplace",
            "the failure claims no recovery marketplace at all",
        )
        named = re.findall(r"rollback-[0-9a-f-]{36}", output)
        self.assertTrue(named, f"the failure names no recovery directory: {output}")
        surviving = {path.name for path in self.recovery_directories()}
        self.assertEqual(
            set(named),
            surviving,
            f"the failure names {sorted(set(named))}, on disk: {sorted(surviving)}",
        )

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
        self.assert_named_recovery_exists(result)

    def test_recovery_survives_when_the_remote_restore_did_not_verify(self) -> None:
        """Restoring from the remote is not the same as having restored.

        The recovery copy is kept when the profile was pointed back at it, and
        dropped when the pinned remote took over -- but "the remote took over"
        was read from the attempt rather than from the verification. When the
        remote marketplace came back and the restored state then failed to
        check out, the copy was deleted and the failure message still told the
        operator to keep it. The one case where recovery material matters most
        is the one that had none.

        The rollback re-add of the previous plugin fails here, so the remote
        marketplace is restored while the plugin inventory is not.
        """
        self.state(
            installed=[self.plugin()],
            marketplaces=[self.marketplace()],
            failures={"plugin add cognitive-powers@cognitive-powers --json": 2},
        )

        result = self.run_installer()

        self.assertNotEqual(result.returncode, 0)
        self.assert_named_recovery_exists(result)

    def test_a_marketplace_restored_to_the_wrong_revision_is_not_a_rollback(
        self,
    ) -> None:
        """The pinned commit is the whole point of restoring from the remote.

        A marketplace that comes back on some other revision is a different
        installation wearing the previous one's name, so the rollback has not
        succeeded and the recovery copy is still the only material that can
        reproduce what was there. With the shim answering the previous commit
        unconditionally this branch could never be entered.
        """
        self.write_git_shim("c" * 40)
        self.state(
            installed=[self.plugin()],
            marketplaces=[self.marketplace()],
            failures={"plugin add cognitive-powers@cognitive-powers --json": 1},
        )

        result = self.run_installer()

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(
            "The previous installation was restored.", self.flattened_output(result)
        )
        self.assert_named_recovery_exists(result)

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

    def installer_path(self) -> Path:
        return INSTALLER

    def installer_argv(self, script: Path | None = None) -> list[str]:
        return [
            "pwsh",
            "-NoProfile",
            "-File",
            str(script or INSTALLER),
            "-ReleaseRef",
            "v1.6.0",
        ]

    def test_scriptblock_source_fails_before_profile_query_or_mutation(self) -> None:
        """The exact shape the README documented, and what it actually did.

        `[scriptblock]::Create($source)` leaves $PSScriptRoot empty, so the
        installer completed the whole transaction and then died on
        `Join-Path $PSScriptRoot` with "Cannot bind argument to parameter 'Path'
        because it is an empty string". The catch rolled the installation back,
        so the documented Windows command installed nothing and blamed an empty
        string for it. CI invoked the file directly and never reproduced it.

        The failure now happens in preflight and names what is missing. Both
        halves matter: an empty log proves nothing was mutated, and the message
        proves the operator is told about the verifier rather than about a
        parameter binding.
        """
        self.state()
        command = (
            "$source = Get-Content -Raw -LiteralPath "
            f"'{INSTALLER.as_posix()}'; "
            "& ([scriptblock]::Create($source)) -ReleaseRef v1.6.0"
        )
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-Command", command],
            env=self.installer_environment(),
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.read_state()["log"], [])
        self.assertIn("no script path of its own", output)
        self.assertNotIn("empty string", output)

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

    def installer_path(self) -> Path:
        return INSTALLER_SH

    def installer_argv(self, script: Path | None = None) -> list[str]:
        return [
            "bash",
            (script or INSTALLER_SH).as_posix(),
            "--release-ref",
            "v1.6.0",
        ]

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


@unittest.skipUnless(
    shutil.which("pwsh") is not None and shutil.which("bash") is not None,
    "comparing the installers needs both pwsh and bash",
)
class InstallerEquivalenceTests(unittest.TestCase):
    """The two installers must issue the same commands, not merely both pass.

    Sharing the scenarios makes each installer meet the same expectations; it
    does not make them meet those expectations the same way. Either could remove
    the plugin before the marketplace instead of after, verify the provisional
    installation a step later, or restore the previous plugins in a different
    order, and both suites would stay green -- while a host that cares about
    ordering saw two different transactions. The port's claim is that it is the
    same transaction, and that claim is about the sequence of calls.
    """

    ABSOLUTE = re.compile(r"^(/|[A-Za-z]:[\\/])")

    def command_log(self, harness, **scenario) -> tuple[int, list[list[str]]]:
        # A TestCase needs a method name that exists; nothing here is run
        # through the unittest machinery, only the fixture is borrowed.
        case = harness("setUp")
        case.setUp()
        try:
            case.state(**scenario)
            result = case.run_installer()
            return result.returncode, self.normalize(case.read_state()["log"])
        finally:
            case.tearDown()

    def normalize(self, log: list[list[str]]) -> list[list[str]]:
        """Collapse the arguments that are fixture locations rather than choices.

        The recovery marketplace is passed by absolute path, and each harness
        runs under its own temporary directory, so those tokens differ by
        construction. Everything that encodes a decision -- the subcommand, the
        plugin id, the pinned ref -- is left exactly as the installer sent it.
        """
        return [
            ["<path>" if self.ABSOLUTE.match(token) else token for token in argv]
            for argv in log
        ]

    def assert_installers_agree(self, **scenario) -> None:
        powershell_code, powershell_log = self.command_log(
            InstallTransactionTests, **scenario
        )
        posix_code, posix_log = self.command_log(InstallShTransactionTests, **scenario)
        self.assertEqual(
            powershell_code == 0,
            posix_code == 0,
            f"install.ps1 exited {powershell_code}, install.sh exited {posix_code}",
        )
        # Two empty logs are equal, so an equality check alone passes loudest
        # when both installers stopped before doing anything -- a preflight that
        # broke on both hosts would read as perfect agreement.
        self.assertTrue(
            powershell_log,
            "neither installer reached the profile; the comparison proves nothing",
        )
        self.assertEqual(
            powershell_log,
            posix_log,
            "the installers issued different command sequences",
        )

    def test_a_clean_install_issues_the_same_commands(self) -> None:
        self.assert_installers_agree()

    def test_a_failed_upgrade_rolls_back_through_the_same_commands(self) -> None:
        case = InstallTransactionTests("setUp")
        case.setUp()
        try:
            prior = [
                case.plugin(),
                case.plugin("cognitive-powers@personal", "1.5.2"),
            ]
            personal = {
                "name": "personal",
                "root": str(case.personal_root),
                "marketplaceSource": {"source": "local"},
            }
            marketplaces = [case.marketplace(), personal]
        finally:
            case.tearDown()
        # The roots inside these records are rewritten by each harness's own
        # state(), so only their shape travels between the two runs.
        self.assert_installers_agree(
            installed=prior,
            marketplaces=marketplaces,
            failures={"plugin add cognitive-powers@cognitive-powers --json": 1},
        )


if __name__ == "__main__":
    unittest.main()
