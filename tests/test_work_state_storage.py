from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "skills" / "execute-durably" / "scripts" / "work_state.py"


def load_work_state():
    spec = importlib.util.spec_from_file_location(
        "test_work_state_storage_module", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


work_state = load_work_state()


class WorkStateStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.workspace = self.base / "workspace"
        self.data_root = self.base / "durable-data"
        self.workspace.mkdir()
        (self.workspace / "source.py").write_text("VALUE = 1\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--root",
                str(self.workspace),
                "--data-root",
                str(self.data_root),
                *arguments,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def initialize(self, session: str) -> Path:
        completed = self.cli(
            "init",
            "--session",
            session,
            "--objective",
            "Exercise durable storage",
            "--criterion",
            "Evidence remains valid",
            "--json",
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return Path(json.loads(completed.stdout)["session_dir"])

    def record(self, session: str, artifact: Path) -> dict[str, object]:
        completed = self.cli(
            "record",
            "--session",
            session,
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--artifact",
            str(artifact),
            "--summary",
            "same artifact",
            "--json",
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return json.loads(completed.stdout)

    def mark_complete(self, session_dir: Path) -> None:
        with work_state.session_lock(session_dir):
            state = work_state.load_state(session_dir)
            state["status"] = "complete"
            state["completed_at"] = work_state.utc_now()
            work_state.save_state_with_event(session_dir, state, "test_completed")

    def test_content_addressed_artifacts_share_one_physical_object(self) -> None:
        self.initialize("first")
        self.initialize("second")
        artifact = self.base / "artifact.bin"
        artifact.write_bytes(b"identical durable artifact\n")

        first = Path(str(self.record("first", artifact)["artifact_copy"]))
        second = Path(str(self.record("second", artifact)["artifact_copy"]))
        digest = work_state._sha256_file(artifact)
        objects = list((self.data_root / "objects" / "sha256").rglob(digest))

        self.assertEqual(len(objects), 1)
        self.assertEqual(first.read_bytes(), artifact.read_bytes())
        self.assertEqual(second.read_bytes(), artifact.read_bytes())
        self.assertEqual(first.stat().st_ino, objects[0].stat().st_ino)
        self.assertEqual(second.stat().st_ino, objects[0].stat().st_ino)
        report = work_state.inspect_storage(self.data_root, largest=5)
        self.assertLess(report["physical_bytes"], report["logical_bytes"])

    def test_shared_cas_corruption_is_observable_in_every_referencing_session(
        self,
    ) -> None:
        self.initialize("first")
        self.initialize("second")
        artifact = self.base / "artifact.bin"
        artifact.write_bytes(b"identical durable artifact\n")
        first = Path(str(self.record("first", artifact)["artifact_copy"]))
        self.record("second", artifact)

        first.write_bytes(b"tampered shared allocation\n")
        second_status = self.cli("status", "--session", "second", "--json")

        self.assertEqual(
            second_status.returncode, 0, second_status.stdout + second_status.stderr
        )
        payload = json.loads(second_status.stdout)
        self.assertEqual(payload["effective_status"], "invalid-evidence")
        self.assertIn(
            "hash no longer matches", payload["criteria"][0]["evidence_error"]
        )

    def test_source_fingerprint_uses_shared_generated_tree_exclusions(self) -> None:
        before = work_state.source_fingerprint(self.workspace, self.data_root)
        for directory in (
            "homes",
            "runs",
            "storage",
            "node_modules",
            ".next",
            "benchmark-output",
        ):
            path = self.workspace / directory
            path.mkdir()
            (path / "large.bin").write_bytes(b"x" * 4096)

        after = work_state.source_fingerprint(self.workspace, self.data_root)

        self.assertEqual(after, before)

    def test_inspection_reports_counts_bytes_projects_sessions_and_largest(
        self,
    ) -> None:
        self.initialize("one")
        self.initialize("two")

        report = work_state.inspect_storage(self.data_root, largest=3)

        self.assertEqual(report["projects"], 1)
        self.assertEqual(report["sessions"], 2)
        self.assertGreater(report["file_count"], 0)
        self.assertGreater(report["bytes"], 0)
        self.assertLessEqual(len(report["largest_directories"]), 3)
        self.assertTrue(
            all(
                {"path", "bytes", "file_count"}.issubset(item)
                for item in report["largest_directories"]
            )
        )

    def test_gc_is_dry_run_and_protects_active_and_keep_last_sessions(self) -> None:
        active = self.initialize("active")
        old = self.initialize("old-complete")
        newest = self.initialize("newest-complete")
        self.mark_complete(old)
        self.mark_complete(newest)
        stale_time = time.time() - (40 * 86400)
        os.utime(old, (stale_time, stale_time))
        os.utime(newest, (stale_time + 10, stale_time + 10))
        os.utime(active, (stale_time, stale_time))

        dry_run = work_state.garbage_collect_storage(
            self.data_root, older_than_days=30, keep_last=1, apply=False
        )

        decisions = {
            Path(item["path"]).name: item for item in dry_run["session_decisions"]
        }
        self.assertEqual(decisions["active"]["decision"], "protect")
        self.assertEqual(decisions["active"]["reason"], "active-session")
        self.assertEqual(decisions["newest-complete"]["decision"], "keep")
        self.assertEqual(decisions["newest-complete"]["reason"], "keep-last")
        self.assertEqual(decisions["old-complete"]["decision"], "delete")
        self.assertTrue(old.is_dir(), "dry-run must not delete")

        applied = work_state.garbage_collect_storage(
            self.data_root, older_than_days=30, keep_last=1, apply=True
        )
        self.assertTrue(applied["applied"])
        self.assertFalse(old.exists())
        self.assertTrue(active.is_dir())
        self.assertTrue(newest.is_dir())

    def test_gc_protects_completed_session_with_live_lock(self) -> None:
        session_dir = self.initialize("locked")
        self.mark_complete(session_dir)
        stale_time = time.time() - (40 * 86400)
        os.utime(session_dir, (stale_time, stale_time))

        with work_state.session_lock(session_dir):
            report = work_state.garbage_collect_storage(
                self.data_root, older_than_days=30, keep_last=0, apply=False
            )

        decision = next(
            item
            for item in report["session_decisions"]
            if Path(item["path"]).name == "locked"
        )
        self.assertEqual(decision["decision"], "protect")
        self.assertEqual(decision["reason"], "live-lock")

    def test_gc_collects_only_unreferenced_old_cas_objects(self) -> None:
        self.initialize("active")
        artifact = self.base / "artifact.bin"
        artifact.write_bytes(b"referenced\n")
        self.record("active", artifact)
        referenced = work_state._sha256_file(artifact)
        orphan = self.data_root / "objects" / "sha256" / "00" / ("0" * 64)
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"orphan")
        stale_time = time.time() - (40 * 86400)
        os.utime(orphan, (stale_time, stale_time))

        report = work_state.garbage_collect_storage(
            self.data_root, older_than_days=30, keep_last=0, apply=True
        )

        object_decisions = {
            item["sha256"]: item["decision"] for item in report["object_decisions"]
        }
        self.assertEqual(object_decisions[referenced], "protect")
        self.assertEqual(object_decisions["0" * 64], "delete")
        self.assertFalse(orphan.exists())

    def test_storage_cli_is_supported_and_gc_requires_apply(self) -> None:
        self.initialize("cli")
        inspected = self.cli("storage-inspect", "--largest", "2", "--json")
        collected = self.cli(
            "storage-gc",
            "--older-than-days",
            "0",
            "--keep-last",
            "0",
            "--json",
        )

        self.assertEqual(inspected.returncode, 0, inspected.stdout + inspected.stderr)
        self.assertEqual(collected.returncode, 0, collected.stdout + collected.stderr)
        self.assertFalse(json.loads(collected.stdout)["applied"])


class SessionDirectoryStabilityTests(unittest.TestCase):
    """One workspace maps to one durable store, however its root is spelled.

    project_key digests the root, so before session_directory canonicalized
    its arguments a caller-spelled root (dot segments, 8.3 short names,
    /var vs /private/var) landed the same workspace in a second store and the
    session's ledger read back empty.
    """

    def test_session_directory_is_stable_across_root_spellings(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw).resolve()
            workspace = base / "workspace"
            workspace.mkdir()
            data_root = base / "durable-data"
            spellings = (
                workspace,
                base / "workspace" / ".." / "workspace",
                base / "." / "workspace",
            )
            directories = {
                work_state.session_directory(spelling, data_root, "session")
                for spelling in spellings
            }
        self.assertEqual(
            len(directories),
            1,
            "one workspace resolved to more than one durable store: "
            f"{sorted(str(item) for item in directories)}",
        )

    def test_symlinked_data_root_inside_the_workspace_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw).resolve()
            workspace = base / "workspace"
            (workspace / "data").mkdir(parents=True)
            link = base / "outside-looking-link"
            try:
                link.symlink_to(workspace / "data", target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"directory symlinks are unavailable: {error}")
            with self.assertRaisesRegex(
                work_state.WorkStateError, "outside the workspace"
            ):
                work_state.session_directory(workspace, link, "session")


class CommandResolutionTests(unittest.TestCase):
    """run must resolve argv[0] as a shell would, and separate launch failures.

    CreateProcess only appends .exe to a bare name, so "npm" and every other
    .cmd shim raised FileNotFoundError on Windows and the receipt recorded a
    failed criterion for a command that never started.
    """

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.workspace = self.base / "workspace"
        self.data_root = self.base / "durable-data"
        self.workspace.mkdir()
        self.bin = self.base / "bin"
        self.bin.mkdir()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def shim(self, name: str) -> None:
        if os.name == "nt":
            (self.bin / f"{name}.cmd").write_text(
                "@echo shim ran\r\n@exit /b 0\r\n", encoding="utf-8"
            )
            return
        path = self.bin / name
        path.write_text("#!/bin/sh\necho shim ran\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)

    def cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PATH"] = str(self.bin) + os.pathsep + environment["PATH"]
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--root",
                str(self.workspace),
                "--data-root",
                str(self.data_root),
                *arguments,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )

    def initialize(self) -> None:
        completed = self.cli(
            "init",
            "--session",
            "resolution",
            "--objective",
            "Resolve commands portably",
            "--criterion",
            "The command receipt is honest",
            "--json",
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_a_cmd_shim_resolves_and_claims(self) -> None:
        self.initialize()
        self.shim("cp-fake-build-tool")
        completed = self.cli(
            "run",
            "--session",
            "resolution",
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--json",
            "--",
            "cp-fake-build-tool",
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "claimed")
        receipt = json.loads(Path(payload["receipt"]).read_text(encoding="utf-8"))
        self.assertTrue(receipt["launched"])
        self.assertEqual(receipt["exit_code"], 0)

    def test_an_unstartable_command_is_marked_unlaunched(self) -> None:
        self.initialize()
        completed = self.cli(
            "run",
            "--session",
            "resolution",
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--json",
            "--",
            "cp-absent-build-tool",
        )
        self.assertNotEqual(completed.returncode, 0)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "failed")
        receipt = json.loads(Path(payload["receipt"]).read_text(encoding="utf-8"))
        self.assertFalse(receipt["launched"])
        self.assertIn("not executable on this host", receipt["stderr_tail"])


class LedgerUnicodeSeparatorTests(unittest.TestCase):
    """One physical ledger line is one record, whatever text a record carries.

    json.dumps(ensure_ascii=False) leaves U+2028, U+2029, and U+0085 raw
    inside a record, and str.splitlines() breaks on all three, so a single
    pasted separator turned one event into two malformed lines and left the
    session permanently unreadable -- starting at init itself, which re-reads
    the ledger it has just written.
    """

    SEPARATED = "plan\u2028the\u2029next\u0085steps"

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.workspace = self.base / "workspace"
        self.data_root = self.base / "durable-data"
        self.workspace.mkdir()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        # Force the legacy Windows console codepage so the CI condition is
        # the condition everywhere: the tool must survive it, not the locale.
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "cp1252"
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--root",
                str(self.workspace),
                "--data-root",
                str(self.data_root),
                *arguments,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )

    def test_init_survives_unicode_line_separators_in_free_text(self) -> None:
        initialized = self.cli(
            "init",
            "--session",
            "separated",
            "--objective",
            self.SEPARATED,
            "--criterion",
            f"criterion {self.SEPARATED}",
            "--json",
        )
        self.assertEqual(
            initialized.returncode, 0, initialized.stdout + initialized.stderr
        )
        session_dir = Path(json.loads(initialized.stdout)["session_dir"])

        status = self.cli("status", "--session", "separated", "--json")
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        self.assertEqual(json.loads(status.stdout)["objective"], self.SEPARATED)

        events = work_state._read_ledger_events(session_dir)
        raw = (session_dir / "ledger.jsonl").read_text(encoding="utf-8")
        self.assertEqual(len(events), raw.count("\n"))

    def test_interior_blank_ledger_lines_are_still_malformed(self) -> None:
        initialized = self.cli(
            "init",
            "--session",
            "strict",
            "--objective",
            "keep interior corruption visible",
            "--criterion",
            "the reader stays strict",
            "--json",
        )
        self.assertEqual(
            initialized.returncode, 0, initialized.stdout + initialized.stderr
        )
        session_dir = Path(json.loads(initialized.stdout)["session_dir"])
        ledger = session_dir / "ledger.jsonl"
        with ledger.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n")

        with self.assertRaisesRegex(work_state.WorkStateError, "malformed"):
            work_state._read_ledger_events(session_dir)


if __name__ == "__main__":
    unittest.main()
