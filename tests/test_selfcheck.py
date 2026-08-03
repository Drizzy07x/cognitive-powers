"""The self-check exists to observe behaviour, so it must not fake a pass."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "selfcheck.py"


def load_module():
    spec = importlib.util.spec_from_file_location("test_selfcheck_module", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


selfcheck = load_module()


MARKETPLACE = "cognitive-powers"


def write_plugin_tree(root: Path, version: str) -> Path:
    """A tree shaped enough for the check to read a version off it."""
    for directory in (".claude-plugin", ".codex-plugin"):
        manifest = root / directory / "plugin.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps({"name": "cognitive-powers", "version": version}),
            encoding="utf-8",
        )
    return root


def cached_root(home: Path, version: str) -> Path:
    return home / "plugins" / "cache" / MARKETPLACE / "cognitive-powers" / version


def write_claude_home(home: Path, *, activated: str, cached: tuple[str, ...]) -> Path:
    """A Claude Code profile: an explicit version pin beside its version cache."""
    record = home / "plugins" / "installed_plugins.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    f"cognitive-powers@{MARKETPLACE}": [
                        {
                            "scope": "user",
                            "installPath": str(cached_root(home, activated)),
                            "version": activated,
                            "lastUpdated": "2026-08-01T06:14:36.998Z",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    for version in cached:
        write_plugin_tree(cached_root(home, version), version)
    return home


def write_codex_home(
    home: Path, *, cached: tuple[str, ...], enabled: bool = True
) -> Path:
    """A Codex home: config.toml states only whether the plugin is enabled."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(
        f"[marketplaces.{MARKETPLACE}]\n"
        'source_type = "git"\n'
        'source = "https://github.com/Drizzy07x/cognitive-powers.git"\n\n'
        f'[plugins."cognitive-powers@{MARKETPLACE}"]\n'
        f"enabled = {str(enabled).lower()}\n",
        encoding="utf-8",
    )
    for version in cached:
        write_plugin_tree(cached_root(home, version), version)
    return home


class SelfCheckTests(unittest.TestCase):
    def test_this_checkout_passes_every_check_about_the_tree(self) -> None:
        """The host checks answer about this machine, not about this checkout.

        Working on the next version while an older one is activated is ordinary,
        and a development checkout sits under neither host's plugin cache, so
        folding those verdicts in here would make the suite report on the
        machine it runs on. All three of their states are reachable on any
        machine through the fixture records in HostActivationTests.
        """
        report = selfcheck.run_checks()
        failed = [
            check
            for check in report["checks"]
            if check["status"] == "fail" and not check["name"].startswith("host.")
        ]
        self.assertEqual(failed, [], failed)
        self.assertTrue(report["observed"])

    def test_it_reports_the_checks_that_prove_the_install_runs(self) -> None:
        names = {check["name"] for check in selfcheck.run_checks()["checks"]}
        for required in (
            "interpreter",
            "host.claude_code",
            "host.codex",
            "hooks.post_tool_use",
            "hooks.stop",
            "hooks.session_start",
            "evidence.shared_root",
            "evidence.round_trip",
        ):
            with self.subTest(check=required):
                self.assertIn(required, names)

    def test_the_report_names_the_version_of_the_root_it_checked(self) -> None:
        """A version the host can be compared against, not just a path."""
        declared = json.loads(
            (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )["version"]
        self.assertEqual(selfcheck.run_checks()["declaredVersion"], declared)

    def test_a_failing_host_check_reaches_the_verdict(self) -> None:
        """Otherwise the check reports drift while the report still says passed."""
        original = selfcheck.check_host_activation
        selfcheck.check_host_activation = lambda: [
            selfcheck.CheckResult("host.claude_code", "fail", "activated 1.7.4")
        ]
        try:
            report = selfcheck.run_checks()
        finally:
            selfcheck.check_host_activation = original
        self.assertFalse(report["passed"])
        self.assertEqual(report["failedCount"], 1)

    def test_a_missing_optional_provider_is_never_a_failure(self) -> None:
        """Absence is a supported configuration; reporting it as broken misleads."""
        original = selfcheck.shutil.which
        selfcheck.shutil.which = lambda name: None
        try:
            results = selfcheck.check_optional_providers()
        finally:
            selfcheck.shutil.which = original
        self.assertTrue(results)
        for result in results:
            with self.subTest(check=result["name"]):
                self.assertEqual(result["status"], "skipped")

    def test_it_names_what_only_the_model_can_observe(self) -> None:
        """A script beside the host cannot see the host's own skill listing."""
        required = selfcheck.run_checks()["hostObservationsRequired"]
        self.assertEqual(len(required), 2)
        self.assertTrue(all(isinstance(item, str) and item for item in required))

    def test_the_checks_leave_nothing_behind_in_the_plugin(self) -> None:
        before = {path for path in PLUGIN_ROOT.rglob("*") if path.is_file()}
        selfcheck.run_checks()
        after = {path for path in PLUGIN_ROOT.rglob("*") if path.is_file()}
        self.assertEqual(after - before, set())

    def test_the_cli_emits_a_machine_readable_report(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        report = json.loads(completed.stdout)
        self.assertEqual(report["kind"], "cognitive_powers_selfcheck")
        self.assertEqual(completed.returncode, 0 if report["passed"] else 1)

    def test_a_broken_check_is_reported_rather_than_raised(self) -> None:
        original = selfcheck.check_interpreter
        selfcheck.check_interpreter = lambda: (_ for _ in ()).throw(
            RuntimeError("probe exploded")
        )
        try:
            code = selfcheck.main(["--json"])
        finally:
            selfcheck.check_interpreter = original
        self.assertEqual(code, 1)


class HostActivationTests(unittest.TestCase):
    """Real host records, driven through the check in every state it can meet.

    The three states are the same on both hosts -- the record matches the tree,
    it names another tree, or there is no record -- but each host reaches them
    by its own rule, so both are exercised separately rather than one standing
    in for the other.
    """

    def home(self) -> Path:
        return Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()

    def test_claude_code_matching_record_passes(self) -> None:
        home = write_claude_home(self.home(), activated="1.8.2", cached=("1.8.2",))
        result = selfcheck.check_claude_code_activation(
            home, cached_root(home, "1.8.2")
        )
        self.assertEqual(result["status"], "pass", result["detail"])
        self.assertEqual(result["activatedVersion"], "1.8.2")
        self.assertEqual(result["checkedVersion"], "1.8.2")

    def test_claude_code_root_the_host_does_not_load_fails(self) -> None:
        """A green report about a cached copy the pin does not name."""
        home = write_claude_home(
            self.home(), activated="1.8.2", cached=("1.8.1", "1.8.2")
        )
        result = selfcheck.check_claude_code_activation(
            home, cached_root(home, "1.8.1")
        )
        self.assertEqual(result["status"], "fail")
        self.assertIn("1.8.1", result["detail"])
        self.assertIn("1.8.2", result["detail"])

    def test_claude_code_absent_record_is_skipped_with_a_stated_reason(self) -> None:
        """A staged tree and CI have no record, and that is supported."""
        home = self.home()
        result = selfcheck.check_claude_code_activation(home, self.home())
        self.assertEqual(result["status"], "skipped")
        self.assertIn(
            str(home / "plugins" / "installed_plugins.json"), result["detail"]
        )

    def test_a_newer_cached_copy_that_was_never_activated_fails(self) -> None:
        """The state this check was written from: 1.8.1 complete, 1.7.4 live.

        Every other check passed against the pinned tree, because the pinned
        tree was healthy. What was wrong was which tree the host had loaded.
        """
        home = write_claude_home(
            self.home(),
            activated="1.7.4",
            cached=("1.7.0", "1.7.1", "1.7.2", "1.7.3", "1.7.4", "1.8.1"),
        )
        result = selfcheck.check_claude_code_activation(
            home, cached_root(home, "1.7.4")
        )
        self.assertEqual(result["status"], "fail")
        self.assertIn("1.7.4", result["detail"])
        self.assertIn("1.8.1", result["detail"])

    def test_an_older_cached_copy_beside_the_active_one_is_not_a_fault(self) -> None:
        """Claude Code keeps every version it ever fetched; upgrading is normal."""
        home = write_claude_home(
            self.home(), activated="1.8.2", cached=("1.7.4", "1.8.1", "1.8.2")
        )
        result = selfcheck.check_claude_code_activation(
            home, cached_root(home, "1.8.2")
        )
        self.assertEqual(result["status"], "pass", result["detail"])

    def test_an_unreadable_claude_record_fails_rather_than_skipping(self) -> None:
        """Skipping would answer a corrupt profile the way it answers an empty one."""
        home = self.home()
        record = home / "plugins" / "installed_plugins.json"
        record.parent.mkdir(parents=True)
        record.write_text("{ not json", encoding="utf-8")
        result = selfcheck.check_claude_code_activation(home, self.home())
        self.assertEqual(result["status"], "fail")

    def test_two_recorded_claude_installations_are_unresolvable(self) -> None:
        home = write_claude_home(self.home(), activated="1.8.2", cached=("1.8.2",))
        record = home / "plugins" / "installed_plugins.json"
        document = json.loads(record.read_text(encoding="utf-8"))
        document["plugins"]["cognitive-powers@personal"] = document["plugins"][
            f"cognitive-powers@{MARKETPLACE}"
        ]
        record.write_text(json.dumps(document), encoding="utf-8")
        result = selfcheck.check_claude_code_activation(
            home, cached_root(home, "1.8.2")
        )
        self.assertEqual(result["status"], "fail")
        self.assertIn("cognitive-powers@personal", result["detail"])

    def test_codex_matching_record_passes(self) -> None:
        home = write_codex_home(self.home(), cached=("1.8.2",))
        result = selfcheck.check_codex_activation(home, cached_root(home, "1.8.2"))
        self.assertEqual(result["status"], "pass", result["detail"])
        self.assertEqual(result["activatedVersion"], "1.8.2")

    def test_codex_root_the_host_does_not_load_fails(self) -> None:
        """Codex resolves the highest cached version, so an older root is not it."""
        home = write_codex_home(self.home(), cached=("1.8.1", "1.8.2"))
        result = selfcheck.check_codex_activation(home, cached_root(home, "1.8.1"))
        self.assertEqual(result["status"], "fail")
        self.assertIn("1.8.1", result["detail"])
        self.assertIn("1.8.2", result["detail"])

    def test_codex_resolves_the_highest_version_not_the_longest_string(self) -> None:
        """1.8.10 is newer than 1.8.9, and sorts before it as text."""
        home = write_codex_home(self.home(), cached=("1.8.9", "1.8.10"))
        result = selfcheck.check_codex_activation(home, cached_root(home, "1.8.10"))
        self.assertEqual(result["status"], "pass", result["detail"])
        self.assertEqual(result["activatedVersion"], "1.8.10")

    def test_codex_absent_record_is_skipped_with_a_stated_reason(self) -> None:
        home = self.home()
        result = selfcheck.check_codex_activation(home, self.home())
        self.assertEqual(result["status"], "skipped")
        self.assertIn(str(home / "config.toml"), result["detail"])

    def test_a_codex_home_with_no_plugins_table_is_skipped(self) -> None:
        home = self.home()
        (home / "config.toml").write_text(
            '[marketplaces.other]\nsource_type = "git"\n', encoding="utf-8"
        )
        result = selfcheck.check_codex_activation(home, self.home())
        self.assertEqual(result["status"], "skipped")

    def test_a_disabled_codex_installation_fails(self) -> None:
        """Installed and not enabled reaches the model as nothing at all."""
        home = write_codex_home(self.home(), cached=("1.8.2",), enabled=False)
        result = selfcheck.check_codex_activation(home, cached_root(home, "1.8.2"))
        self.assertEqual(result["status"], "fail")
        self.assertIn("disabled", result["detail"])

    def test_an_enabled_codex_plugin_with_nothing_cached_fails(self) -> None:
        home = write_codex_home(self.home(), cached=())
        result = selfcheck.check_codex_activation(home, self.home())
        self.assertEqual(result["status"], "fail")

    def test_a_development_checkout_is_compared_to_no_host_root(self) -> None:
        """Outside both caches, only the record's own consistency is assertable.

        A checkout at the next version is not a stale installation, and reporting
        it as one would train the reader to ignore the check.
        """
        home = write_claude_home(self.home(), activated="1.8.2", cached=("1.8.2",))
        checkout = write_plugin_tree(self.home() / "checkout", "1.9.0")
        result = selfcheck.check_claude_code_activation(home, checkout)
        self.assertEqual(result["status"], "pass", result["detail"])
        self.assertEqual(result["activatedVersion"], "1.8.2")
        self.assertEqual(result["checkedVersion"], "1.9.0")


if __name__ == "__main__":
    unittest.main()
