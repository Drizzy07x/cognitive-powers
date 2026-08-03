"""The documented clone command is only useful if its ref resolves.

Every case here drives `check_release_ref_exists` through a stub `git`, so the
suite stays offline while the failure it exists to catch -- a README naming a
tag the remote does not publish -- is exercised directly rather than described.
The one test that reads the real README asserts only its shape, which is what
keeps the regex from quietly matching nothing after a rewrite.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "check_release_ref_exists.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "test_release_ref_exists_module", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = load_module()

REMOTE = "https://github.com/Drizzy07x/cognitive-powers"


def write_readme(root: Path, ref: str = "v1.9.0", extra: str = "") -> None:
    (root / "README.md").write_text(
        f"Install from a checkout:\n\n"
        f"```powershell\ngit clone --branch {ref} --depth 1 {REMOTE}\n```\n"
        f"{extra}",
        encoding="utf-8",
    )


def stub_git(stdout: str = "", returncode: int = 0, raises: Exception | None = None):
    """A `subprocess.run` stand-in that answers exactly once, however told to."""

    def runner(argv, **_kwargs):
        if raises is not None:
            raise raises
        runner.calls.append(argv)
        return subprocess.CompletedProcess(argv, returncode, stdout, "boom")

    runner.calls = []
    return runner


class DocumentedRefTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_a_tag_the_remote_publishes_resolves(self) -> None:
        write_readme(self.root)
        runner = stub_git("a1b2c3\trefs/tags/v1.9.0\n")
        payload = gate.check(self.root, runner=runner)
        self.assertEqual(payload["ref"], "v1.9.0")
        self.assertEqual(payload["repository"], REMOTE)
        self.assertEqual(runner.calls[0][:3], ["git", "ls-remote", "--tags"])

    def test_an_annotated_tag_resolves_through_its_peeled_ref(self) -> None:
        """`refs/tags/x^{}` alone is a published tag, not a miss."""
        write_readme(self.root)
        runner = stub_git("a1b2c3\trefs/tags/v1.9.0^{}\n")
        self.assertTrue(gate.check(self.root, runner=runner)["resolved"])

    def test_a_tag_the_remote_does_not_publish_fails(self) -> None:
        """The 1.8.2 state: every carrier agrees, and the clone command 404s."""
        write_readme(self.root, ref="v1.8.2")
        with self.assertRaises(gate.ReleaseRefError) as raised:
            gate.check(self.root, runner=stub_git(""))
        self.assertIn("publishes no tag v1.8.2", str(raised.exception))

    def test_a_longer_tag_sharing_the_prefix_is_not_a_match(self) -> None:
        """`v1.9.0-rc1` existing says nothing about `v1.9.0` existing."""
        write_readme(self.root)
        with self.assertRaises(gate.ReleaseRefError):
            gate.check(self.root, runner=stub_git("a1b2c3\trefs/tags/v1.9.0-rc1\n"))

    def test_a_remote_that_will_not_answer_fails_closed(self) -> None:
        write_readme(self.root)
        with self.assertRaises(gate.ReleaseRefError) as raised:
            gate.check(self.root, runner=stub_git(returncode=128))
        self.assertIn("exited 128", str(raised.exception))

    def test_a_git_that_cannot_run_fails_closed(self) -> None:
        write_readme(self.root)
        with self.assertRaises(gate.ReleaseRefError) as raised:
            gate.check(self.root, runner=stub_git(raises=OSError("no git")))
        self.assertIn("cannot query", str(raised.exception))

    def test_a_timeout_fails_closed(self) -> None:
        write_readme(self.root)
        expired = subprocess.TimeoutExpired(cmd="git", timeout=60)
        with self.assertRaises(gate.ReleaseRefError):
            gate.check(self.root, runner=stub_git(raises=expired))

    def test_a_readme_carrying_no_clone_command_fails(self) -> None:
        """Nothing to check is a failure; otherwise deleting the command passes."""
        (self.root / "README.md").write_text("Install it somehow.\n", encoding="utf-8")
        with self.assertRaises(gate.ReleaseRefError) as raised:
            gate.check(self.root, runner=stub_git("a\trefs/tags/v1.9.0\n"))
        self.assertIn("no `git clone --branch` command", str(raised.exception))

    def test_two_clone_commands_naming_different_tags_fail(self) -> None:
        """The POSIX and PowerShell examples are one release or they are a bug."""
        write_readme(
            self.root,
            extra=f"```bash\ngit clone --branch v1.8.1 --depth 1 {REMOTE}\n```\n",
        )
        with self.assertRaises(gate.ReleaseRefError) as raised:
            gate.check(self.root, runner=stub_git("a\trefs/tags/v1.9.0\n"))
        self.assertIn("divergent clone commands", str(raised.exception))

    def test_a_tag_build_refuses_a_readme_naming_another_release(self) -> None:
        write_readme(self.root, ref="v1.8.1")
        with self.assertRaises(gate.ReleaseRefError) as raised:
            gate.check(
                self.root,
                expect_ref="v1.9.0",
                runner=stub_git("a\trefs/tags/v1.8.1\n"),
            )
        self.assertIn("release under build is v1.9.0", str(raised.exception))

    def test_a_malformed_ref_is_rejected_before_the_network(self) -> None:
        write_readme(self.root, ref="main")
        runner = stub_git("a\trefs/tags/main\n")
        with self.assertRaises(gate.ReleaseRefError):
            gate.check(self.root, runner=runner)
        self.assertEqual(runner.calls, [])

    def test_the_entrypoint_reports_the_refusal_and_exits_two(self) -> None:
        write_readme(self.root, ref="v1.8.2")
        original = gate.subprocess.run
        gate.subprocess.run = stub_git("")
        try:
            code = gate.main(["--root", str(self.root)])
        finally:
            gate.subprocess.run = original
        self.assertEqual(code, 2)


class RepositoryShapeTests(unittest.TestCase):
    """Offline, and the half that keeps the networked job pointed at something.

    A README rewrite that stops matching the pattern would leave the nightly
    job resolving a ref it never found; this fails on the push that made it so.
    """

    def test_the_readme_documents_exactly_one_clone_target(self) -> None:
        targets = gate.documented_clone_targets(PLUGIN_ROOT)
        self.assertEqual(len(targets), 1)
        url, ref = targets[0]
        self.assertEqual(url, REMOTE)
        declared = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )["version"].split("+")[0]
        self.assertEqual(ref, f"v{declared}")

    def test_the_workflow_runs_this_gate_on_release_like_refs(self) -> None:
        """The rule is only a gate where CI actually invokes it."""
        workflow = (PLUGIN_ROOT / ".github" / "workflows" / "validate.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("documented-release-ref:", workflow)
        job = workflow[workflow.index("  documented-release-ref:") :]
        self.assertIn("scripts/check_release_ref_exists.py", job)
        self.assertIn("--expect-ref", job)
        self.assertIn("github.event_name == 'schedule'", job)
        self.assertIn("startsWith(github.ref, 'refs/tags/')", job)

    def test_the_offline_entrypoint_stays_offline(self) -> None:
        """Adding a network call to `validate_all.py` would defeat the split."""
        validator = (PLUGIN_ROOT / "scripts" / "validate_all.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("check_release_ref_exists", validator)


if __name__ == "__main__":
    unittest.main()
