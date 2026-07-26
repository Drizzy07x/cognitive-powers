from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path(
    os.environ.get(
        "COGNITIVE_WORK_STATE_SCRIPT",
        PLUGIN_ROOT / "skills" / "execute-durably" / "scripts" / "work_state.py",
    )
)


def load_work_state():
    spec = importlib.util.spec_from_file_location("test_work_state_module", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


work_state = load_work_state()


class WorkStateTests(unittest.TestCase):
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

    def initialize(self, session: str = "demo") -> dict[str, object]:
        completed = self.cli(
            "init",
            "--session",
            session,
            "--objective",
            "Prove the requested behavior",
            "--criterion",
            "The behavioral command succeeds",
            "--json",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def claim_with_passing_command(
        self, session: str = "demo"
    ) -> subprocess.CompletedProcess[str]:
        return self.cli(
            "run",
            "--session",
            session,
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--json",
            "--",
            sys.executable,
            "-c",
            "print('observable success')",
        )

    def plan_packets(self, packets: list[dict[str, object]], session: str = "demo"):
        plan = self.base / f"{session}-packet-plan.json"
        plan.write_text(
            json.dumps({"schema_version": 1, "packets": packets}),
            encoding="utf-8",
        )
        return self.cli(
            "plan-packets",
            "--session",
            session,
            "--plan",
            str(plan),
            "--json",
        )

    def packet_spec(
        self,
        packet_id: str,
        owned_path: str,
        *,
        dependencies: list[str] | None = None,
        check: list[str] | None = None,
    ) -> dict[str, object]:
        return {
            "id": packet_id,
            "objective": f"Complete {packet_id}",
            "owned_paths": [owned_path],
            "dependencies": dependencies or [],
            "invariants": ["Preserve the public behavior"],
            "checks": [check or [sys.executable, "-c", "print('packet passed')"]],
            "integration_notes": ["Run the integrated criterion afterward"],
        }

    def test_owned_path_property_rejects_traversal_and_absolute_forms(self) -> None:
        invalid_paths = (
            "",
            ".",
            "..",
            "../source.py",
            "src/../source.py",
            "/absolute/source.py",
            "\\absolute\\source.py",
            "C:/absolute/source.py",
            "C:\\absolute\\source.py",
        )
        for value in invalid_paths:
            with self.subTest(value=value):
                with self.assertRaises(work_state.WorkStateError):
                    work_state._normalize_owned_path(value)

        valid_paths = ("source.py", "src/module.py", ".config/policy.json")
        self.assertEqual(
            [work_state._normalize_owned_path(value) for value in valid_paths],
            list(valid_paths),
        )

    def test_packet_plan_rejects_overlapping_ownership_atomically(self) -> None:
        self.initialize()
        planned = self.plan_packets(
            [
                self.packet_spec("p1", "src"),
                self.packet_spec("p2", "src/module.py"),
            ]
        )

        self.assertEqual(planned.returncode, 2)
        self.assertIn("overlap ownership", planned.stdout)
        status = json.loads(self.cli("status", "--session", "demo", "--json").stdout)
        self.assertEqual(status["work_packets"], [])

    def test_packet_plan_rejects_dependency_cycles(self) -> None:
        self.initialize()
        (self.workspace / "other.py").write_text("OTHER = 1\n", encoding="utf-8")

        planned = self.plan_packets(
            [
                self.packet_spec("p1", "source.py", dependencies=["p2"]),
                self.packet_spec("p2", "other.py", dependencies=["p1"]),
            ]
        )

        self.assertEqual(planned.returncode, 2)
        self.assertIn("dependency cycle", planned.stdout)

    def test_packet_dependencies_checks_and_global_completion_are_separate(
        self,
    ) -> None:
        self.initialize()
        (self.workspace / "other.py").write_text("OTHER = 1\n", encoding="utf-8")
        planned = self.plan_packets(
            [
                self.packet_spec("p1", "source.py"),
                self.packet_spec("p2", "other.py", dependencies=["p1"]),
            ]
        )
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)

        blocked = self.cli(
            "start-packet",
            "--session",
            "demo",
            "--packet",
            "p2",
            "--owner",
            "worker-two",
            "--json",
        )
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("dependency p1 is not completed", blocked.stdout)

        for packet_id, owner in (("p1", "worker-one"), ("p2", "worker-two")):
            started = self.cli(
                "start-packet",
                "--session",
                "demo",
                "--packet",
                packet_id,
                "--owner",
                owner,
                "--json",
            )
            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
            checked = self.cli(
                "run-packet-check",
                "--session",
                "demo",
                "--packet",
                packet_id,
                "--check",
                "k1",
                "--executor",
                owner,
                "--json",
            )
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            completed = self.cli(
                "complete-packet",
                "--session",
                "demo",
                "--packet",
                packet_id,
                "--actor",
                owner,
                "--json",
            )
            self.assertEqual(
                completed.returncode, 0, completed.stdout + completed.stderr
            )
            self.assertEqual(json.loads(completed.stdout)["session_status"], "active")

        global_completion = self.cli("complete", "--session", "demo", "--json")
        self.assertEqual(global_completion.returncode, 2)
        self.assertIn("c1:pending", global_completion.stdout)

        self.assertEqual(self.claim_with_passing_command().returncode, 0)
        verified = self.cli(
            "verify",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--verifier",
            "reviewer",
            "--verdict",
            "confirmed",
            "--note",
            "The integrated command passed",
            "--json",
        )
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        global_completion = self.cli("complete", "--session", "demo", "--json")
        self.assertEqual(global_completion.returncode, 0, global_completion.stdout)

    def test_packet_check_retries_abandoned_in_progress_runner(self) -> None:
        initialized = self.initialize()
        planned = self.plan_packets([self.packet_spec("p1", "source.py")])
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
        started = self.cli(
            "start-packet",
            "--session",
            "demo",
            "--packet",
            "p1",
            "--owner",
            "worker",
            "--json",
        )
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        dead_pid = process.pid
        self.assertEqual(process.wait(timeout=5), 0)
        state_path = Path(str(initialized["state"]))
        state = json.loads(state_path.read_text(encoding="utf-8"))
        check = state["work_packets"][0]["checks"][0]
        check.update(
            {
                "status": "in_progress",
                "attempts": 1,
                "executor": "worker",
                "runner_pid": dead_pid,
            }
        )
        state_path.write_text(json.dumps(state), encoding="utf-8")

        retried = self.cli(
            "run-packet-check",
            "--session",
            "demo",
            "--packet",
            "p1",
            "--check",
            "k1",
            "--executor",
            "worker",
            "--json",
        )

        self.assertEqual(retried.returncode, 0, retried.stdout + retried.stderr)
        final = json.loads(state_path.read_text(encoding="utf-8"))
        final_check = final["work_packets"][0]["checks"][0]
        self.assertEqual(final_check["status"], "passed")
        self.assertEqual(final_check["attempts"], 2)
        self.assertIsNone(final_check.get("runner_pid"))

    def test_packet_check_rejects_in_progress_runner_while_pid_is_alive(self) -> None:
        initialized = self.initialize()
        planned = self.plan_packets([self.packet_spec("p1", "source.py")])
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
        started = self.cli(
            "start-packet",
            "--session",
            "demo",
            "--packet",
            "p1",
            "--owner",
            "worker",
            "--json",
        )
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        state_path = Path(str(initialized["state"]))
        state = json.loads(state_path.read_text(encoding="utf-8"))
        check = state["work_packets"][0]["checks"][0]
        check.update(
            {
                "status": "in_progress",
                "attempts": 1,
                "executor": "worker",
                "runner_pid": os.getpid(),
            }
        )
        state_path.write_text(json.dumps(state), encoding="utf-8")

        rejected = self.cli(
            "run-packet-check",
            "--session",
            "demo",
            "--packet",
            "p1",
            "--check",
            "k1",
            "--executor",
            "worker",
            "--json",
        )

        self.assertEqual(rejected.returncode, 2)
        self.assertIn("runner is still alive", rejected.stdout)

    def test_packet_check_recovers_when_pid_identity_was_reused(self) -> None:
        initialized = self.initialize()
        planned = self.plan_packets([self.packet_spec("p1", "source.py")])
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
        started = self.cli(
            "start-packet",
            "--session",
            "demo",
            "--packet",
            "p1",
            "--owner",
            "worker",
            "--json",
        )
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        state_path = Path(str(initialized["state"]))
        state = json.loads(state_path.read_text(encoding="utf-8"))
        check = state["work_packets"][0]["checks"][0]
        check.update(
            {
                "status": "in_progress",
                "attempts": 1,
                "executor": "worker",
                "runner_pid": os.getpid(),
                "runner_identity": "reused-pid-from-another-process",
            }
        )
        state_path.write_text(json.dumps(state), encoding="utf-8")

        retried = self.cli(
            "run-packet-check",
            "--session",
            "demo",
            "--packet",
            "p1",
            "--check",
            "k1",
            "--executor",
            "worker",
            "--json",
        )

        self.assertEqual(retried.returncode, 0, retried.stdout + retried.stderr)

    def test_failed_packet_check_can_retry_but_owned_path_changes_make_it_stale(
        self,
    ) -> None:
        self.initialize()
        command = [
            sys.executable,
            "-c",
            "from pathlib import Path; raise SystemExit(0 if Path('ready.flag').exists() else 3)",
        ]
        planned = self.plan_packets(
            [self.packet_spec("p1", "source.py", check=command)]
        )
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
        started = self.cli(
            "start-packet",
            "--session",
            "demo",
            "--packet",
            "p1",
            "--owner",
            "worker",
            "--json",
        )
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)

        failed = self.cli(
            "run-packet-check",
            "--session",
            "demo",
            "--packet",
            "p1",
            "--check",
            "k1",
            "--executor",
            "worker",
            "--json",
        )
        self.assertEqual(failed.returncode, 3)
        self.assertEqual(json.loads(failed.stdout)["status"], "failed")

        (self.workspace / "ready.flag").write_text("ready\n", encoding="utf-8")
        passed = self.cli(
            "run-packet-check",
            "--session",
            "demo",
            "--packet",
            "p1",
            "--check",
            "k1",
            "--executor",
            "worker",
            "--json",
        )
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)

        (self.workspace / "source.py").write_text("VALUE = 2\n", encoding="utf-8")
        stale = self.cli(
            "complete-packet",
            "--session",
            "demo",
            "--packet",
            "p1",
            "--actor",
            "worker",
            "--json",
        )
        self.assertEqual(stale.returncode, 2)
        self.assertIn("stale for its owned paths", stale.stdout)

    def test_verified_criterion_cannot_bypass_pending_packet(self) -> None:
        self.initialize()
        planned = self.plan_packets([self.packet_spec("p1", "source.py")])
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
        self.assertEqual(self.claim_with_passing_command().returncode, 0)
        verified = self.cli(
            "verify",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--verifier",
            "reviewer",
            "--verdict",
            "confirmed",
            "--note",
            "The integrated command passed",
            "--json",
        )
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)

        completed = self.cli("complete", "--session", "demo", "--json")

        self.assertEqual(completed.returncode, 2)
        self.assertIn("packet:p1:planned", completed.stdout)

    def test_tampered_completed_packet_receipt_blocks_global_completion(self) -> None:
        self.initialize()
        planned = self.plan_packets([self.packet_spec("p1", "source.py")])
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
        self.assertEqual(
            self.cli(
                "start-packet",
                "--session",
                "demo",
                "--packet",
                "p1",
                "--owner",
                "worker",
                "--json",
            ).returncode,
            0,
        )
        checked = self.cli(
            "run-packet-check",
            "--session",
            "demo",
            "--packet",
            "p1",
            "--check",
            "k1",
            "--executor",
            "worker",
            "--json",
        )
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        packet_completed = self.cli(
            "complete-packet",
            "--session",
            "demo",
            "--packet",
            "p1",
            "--actor",
            "worker",
            "--json",
        )
        self.assertEqual(packet_completed.returncode, 0, packet_completed.stdout)
        receipt = Path(json.loads(checked.stdout)["receipt"])
        receipt.write_text(receipt.read_text(encoding="utf-8") + " ", encoding="utf-8")

        status = json.loads(self.cli("status", "--session", "demo", "--json").stdout)

        self.assertEqual(status["effective_status"], "invalid-evidence")
        self.assertIn(
            "receipt hash changed", status["work_packets"][0]["evidence_error"]
        )

    def test_stale_completed_packet_can_reopen_and_revalidate(self) -> None:
        self.initialize()
        self.assertEqual(
            self.plan_packets([self.packet_spec("p1", "source.py")]).returncode,
            0,
        )
        self.assertEqual(
            self.cli(
                "start-packet",
                "--session",
                "demo",
                "--packet",
                "p1",
                "--owner",
                "worker",
                "--json",
            ).returncode,
            0,
        )
        self.assertEqual(
            self.cli(
                "run-packet-check",
                "--session",
                "demo",
                "--packet",
                "p1",
                "--check",
                "k1",
                "--executor",
                "worker",
                "--json",
            ).returncode,
            0,
        )
        self.assertEqual(
            self.cli(
                "complete-packet",
                "--session",
                "demo",
                "--packet",
                "p1",
                "--actor",
                "worker",
                "--json",
            ).returncode,
            0,
        )
        self.workspace.joinpath("source.py").write_text("VALUE = 2\n", encoding="utf-8")

        reopened = self.cli(
            "reopen-packet",
            "--session",
            "demo",
            "--packet",
            "p1",
            "--actor",
            "worker",
            "--reason",
            "owned source changed after formatting",
            "--json",
        )

        self.assertEqual(reopened.returncode, 0, reopened.stdout + reopened.stderr)
        self.assertEqual(json.loads(reopened.stdout)["status"], "active")
        status = json.loads(self.cli("status", "--session", "demo", "--json").stdout)
        check = status["work_packets"][0]["checks"][0]
        self.assertEqual(check["status"], "pending")
        self.assertIsNone(check["receipt"])
        self.assertEqual(
            self.cli(
                "run-packet-check",
                "--session",
                "demo",
                "--packet",
                "p1",
                "--check",
                "k1",
                "--executor",
                "worker",
                "--json",
            ).returncode,
            0,
        )
        self.assertEqual(
            self.cli(
                "complete-packet",
                "--session",
                "demo",
                "--packet",
                "p1",
                "--actor",
                "worker",
                "--json",
            ).returncode,
            0,
        )

    def test_valid_completed_packet_cannot_reopen(self) -> None:
        self.initialize()
        self.assertEqual(
            self.plan_packets([self.packet_spec("p1", "source.py")]).returncode,
            0,
        )
        for command, extra in (
            ("start-packet", ["--owner", "worker"]),
            ("run-packet-check", ["--check", "k1", "--executor", "worker"]),
            ("complete-packet", ["--actor", "worker"]),
        ):
            result = self.cli(
                command,
                "--session",
                "demo",
                "--packet",
                "p1",
                *extra,
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        reopened = self.cli(
            "reopen-packet",
            "--session",
            "demo",
            "--packet",
            "p1",
            "--actor",
            "worker",
            "--reason",
            "retry",
            "--json",
        )

        self.assertEqual(reopened.returncode, 2)
        self.assertIn("evidence is still valid", reopened.stdout)

    def test_reopen_packet_reactivates_session_and_invalidates_descendants(
        self,
    ) -> None:
        self.initialize()
        (self.workspace / "other.py").write_text("OTHER = 1\n", encoding="utf-8")
        (self.workspace / "third.py").write_text("THIRD = 1\n", encoding="utf-8")
        packets = [
            self.packet_spec("p1", "source.py"),
            self.packet_spec("p2", "other.py", dependencies=["p1"]),
            self.packet_spec("p3", "third.py", dependencies=["p2"]),
        ]
        self.assertEqual(self.plan_packets(packets).returncode, 0)
        for packet_id in ("p1", "p2", "p3"):
            for command, extra in (
                ("start-packet", ["--owner", packet_id]),
                ("run-packet-check", ["--check", "k1", "--executor", packet_id]),
                ("complete-packet", ["--actor", packet_id]),
            ):
                result = self.cli(
                    command,
                    "--session",
                    "demo",
                    "--packet",
                    packet_id,
                    *extra,
                    "--json",
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.claim_with_passing_command().returncode, 0)
        self.assertEqual(
            self.cli(
                "verify",
                "--session",
                "demo",
                "--criterion",
                "c1",
                "--verifier",
                "reviewer",
                "--verdict",
                "confirmed",
                "--note",
                "Integrated command passed",
                "--json",
            ).returncode,
            0,
        )
        self.assertEqual(
            self.cli("complete", "--session", "demo", "--json").returncode, 0
        )
        self.workspace.joinpath("source.py").write_text("VALUE = 2\n", encoding="utf-8")

        reopened = self.cli(
            "reopen-packet",
            "--session",
            "demo",
            "--packet",
            "p1",
            "--actor",
            "p1",
            "--reason",
            "upstream source changed",
            "--json",
        )

        self.assertEqual(reopened.returncode, 0, reopened.stdout + reopened.stderr)
        payload = json.loads(reopened.stdout)
        self.assertEqual(payload["session_status"], "active")
        self.assertEqual(payload["invalidated_dependents"], ["p2", "p3"])
        status = json.loads(self.cli("status", "--session", "demo", "--json").stdout)
        by_id = {packet["id"]: packet for packet in status["work_packets"]}
        self.assertEqual(by_id["p1"]["status"], "active")
        self.assertEqual(by_id["p2"]["status"], "planned")
        self.assertEqual(by_id["p3"]["status"], "planned")
        self.assertEqual(by_id["p2"]["checks"][0]["status"], "pending")

    def test_reopen_packet_refuses_active_dependent_without_mutation(self) -> None:
        self.initialize()
        (self.workspace / "other.py").write_text("OTHER = 1\n", encoding="utf-8")
        self.assertEqual(
            self.plan_packets(
                [
                    self.packet_spec("p1", "source.py"),
                    self.packet_spec("p2", "other.py", dependencies=["p1"]),
                ]
            ).returncode,
            0,
        )
        for command, extra in (
            ("start-packet", ["--owner", "worker-one"]),
            ("run-packet-check", ["--check", "k1", "--executor", "worker-one"]),
            ("complete-packet", ["--actor", "worker-one"]),
        ):
            self.assertEqual(
                self.cli(
                    command,
                    "--session",
                    "demo",
                    "--packet",
                    "p1",
                    *extra,
                    "--json",
                ).returncode,
                0,
            )
        self.assertEqual(
            self.cli(
                "start-packet",
                "--session",
                "demo",
                "--packet",
                "p2",
                "--owner",
                "worker-two",
                "--json",
            ).returncode,
            0,
        )
        self.workspace.joinpath("source.py").write_text("VALUE = 2\n", encoding="utf-8")

        reopened = self.cli(
            "reopen-packet",
            "--session",
            "demo",
            "--packet",
            "p1",
            "--actor",
            "worker-one",
            "--reason",
            "upstream changed",
            "--json",
        )

        self.assertEqual(reopened.returncode, 2)
        self.assertIn("dependent work is active", reopened.stdout)
        status = json.loads(self.cli("status", "--session", "demo", "--json").stdout)
        by_id = {packet["id"]: packet for packet in status["work_packets"]}
        self.assertEqual(by_id["p1"]["status"], "completed")
        self.assertEqual(by_id["p2"]["status"], "active")

    def test_legacy_v1_state_without_packets_still_loads(self) -> None:
        session_dir = self.base / "legacy"
        session_dir.mkdir()
        (session_dir / "state.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "session_id": "legacy",
                    "criteria": [],
                    "last_seq": 0,
                }
            ),
            encoding="utf-8",
        )

        state = work_state.load_state(session_dir)

        self.assertEqual(work_state._work_packets(state), [])

    def test_facade_loads_twice_from_isolated_python(self) -> None:
        code = f"""
import importlib.util
import sys
from pathlib import Path
path = Path({str(SCRIPT_PATH)!r})
loaded = []
for name in ('isolated_work_state_one', 'isolated_work_state_two'):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    loaded.append(module)
assert loaded[0].project_key(path.parent) == loaded[1].project_key(path.parent)
assert callable(loaded[0].session_lock)
assert loaded[0].WorkStateError.__name__ == 'WorkStateError'
"""

        completed = subprocess.run(
            [sys.executable, "-I", "-c", code],
            check=False,
            capture_output=True,
            text=True,
            cwd=self.base,
        )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_selective_gate_mutations_are_killed(self) -> None:
        runner = (
            PLUGIN_ROOT
            / "skills"
            / "execute-durably"
            / "scripts"
            / "work_state_core"
            / "mutation_probe.py"
        )

        completed = subprocess.run(
            [
                sys.executable,
                str(runner),
                "--root",
                str(PLUGIN_ROOT),
                "--work-state",
                str(SCRIPT_PATH),
                "--python",
                sys.executable,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["all_mutations_killed"])
        self.assertEqual(len(payload["mutations"]), 2)
        for mutation in payload["mutations"]:
            self.assertTrue(mutation["baseline_passed"], mutation)
            self.assertEqual(mutation["baseline_exit_code"], 0, mutation)
            self.assertNotIn("FileNotFoundError", mutation["output_tail"])
            self.assertIn("AssertionError", mutation["output_tail"])

    def test_real_command_requires_independent_verification_before_completion(
        self,
    ) -> None:
        initialized = self.initialize()
        session_dir = Path(str(initialized["state"])).parent

        self.assertFalse((self.workspace / ".cognitive-powers").exists())
        self.assertFalse(session_dir.is_relative_to(self.workspace))

        claimed = self.claim_with_passing_command()
        self.assertEqual(claimed.returncode, 0, claimed.stderr)
        claim_payload = json.loads(claimed.stdout)
        self.assertEqual(claim_payload["status"], "claimed")
        self.assertEqual(claim_payload["exit_code"], 0)

        self_verified = self.cli(
            "verify",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--verifier",
            "builder",
            "--verdict",
            "confirmed",
            "--note",
            "I ran the receipt command",
            "--json",
        )
        self.assertEqual(self_verified.returncode, 2)
        self.assertIn("cannot verify its own claim", self_verified.stdout)

        verified = self.cli(
            "verify",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--verifier",
            "reviewer",
            "--verdict",
            "confirmed",
            "--note",
            "Receipt command exited zero with captured output",
            "--json",
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)["status"], "verified")

        completed = self.cli("complete", "--session", "demo", "--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["status"], "complete")

        events = [
            json.loads(line)
            for line in (session_dir / "ledger.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(
            [event["event"] for event in events],
            [
                "session_initialized",
                "execution_started",
                "evidence_claimed",
                "verification_confirmed",
                "session_completed",
            ],
        )
        self.assertEqual([event["seq"] for event in events], list(range(1, 6)))

    def test_communication_evidence_copies_provider_record_and_detects_tampering(
        self,
    ) -> None:
        self.initialize("communication")
        provider_record = self.base / "provider-usage.json"
        provider_record.write_text(
            json.dumps(
                {
                    "provider": "fixture-provider",
                    "model": "fixture-model",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 25,
                        "output_tokens": 30,
                    },
                }
            ),
            encoding="utf-8",
        )
        communication_receipt = self.base / "communication-receipt.json"
        communication_receipt.write_text(
            json.dumps(
                {
                    "type": "communication_usage_evidence",
                    "schemaVersion": 1,
                    "taskId": "task-1",
                    "variant": "adaptive",
                    "provider": "fixture-provider",
                    "model": "fixture-model",
                    "success": True,
                    "qualityScore": 95.0,
                    "criticalFailure": False,
                    "usage": {
                        "inputTokens": 100,
                        "cachedInputTokens": 25,
                        "freshInputTokens": 75,
                        "outputTokens": 31,
                        "totalTokens": 130,
                    },
                    "providerRecord": str(provider_record.resolve()),
                    "providerRecordSha256": work_state._sha256_file(provider_record),
                    "counterfactualEstimated": False,
                }
            ),
            encoding="utf-8",
        )
        rejected = self.cli(
            "record-communication",
            "--session",
            "communication",
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--receipt",
            str(communication_receipt),
            "--json",
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("does not match the provider record", rejected.stdout)
        receipt_payload = json.loads(communication_receipt.read_text(encoding="utf-8"))
        receipt_payload["usage"]["outputTokens"] = 30
        communication_receipt.write_text(json.dumps(receipt_payload), encoding="utf-8")
        recorded = self.cli(
            "record-communication",
            "--session",
            "communication",
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--receipt",
            str(communication_receipt),
            "--json",
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        payload = json.loads(recorded.stdout)
        self.assertEqual(payload["usage"]["freshInputTokens"], 75)
        provider_copy = Path(payload["provider_record_copy"])
        self.assertTrue(provider_copy.is_file())

        provider_copy.write_text("tampered\n", encoding="utf-8")
        status = self.cli("status", "--session", "communication", "--json")
        self.assertEqual(status.returncode, 0, status.stderr)
        status_payload = json.loads(status.stdout)
        self.assertEqual(status_payload["effective_status"], "invalid-evidence")
        self.assertIn(
            "provider usage record", status_payload["criteria"][0]["evidence_error"]
        )

    def test_browser_evidence_copies_and_validates_every_declared_artifact(
        self,
    ) -> None:
        initialized = self.initialize()
        run_root = self.base / "browser-run"
        trace = run_root / "test-results" / "flow" / "trace.zip"
        report = run_root / "playwright-report.json"
        trace.parent.mkdir(parents=True)
        trace.write_bytes(b"trace evidence")
        report.write_text('{"stats":{"expected":1}}\n', encoding="utf-8")
        payload = {
            "schema_version": 1,
            "type": "playwright_evidence",
            "provider": "playwright",
            "version": "Version 1.60.0",
            "command": ["playwright", "test"],
            "commandStarted": True,
            "exitCode": 0,
            "passed": True,
            "stats": {"expected": 1, "unexpected": 0, "flaky": 0, "skipped": 0},
            "artifactRoot": str(run_root),
            "artifacts": [
                {
                    "path": "playwright-report.json",
                    "sha256": work_state._sha256_file(report),
                    "bytes": report.stat().st_size,
                },
                {
                    "path": "test-results/flow/trace.zip",
                    "sha256": work_state._sha256_file(trace),
                    "bytes": trace.stat().st_size,
                },
            ],
        }
        browser_receipt = run_root / "cognitive-playwright-receipt.json"
        browser_receipt.write_text(json.dumps(payload), encoding="utf-8")

        recorded = self.cli(
            "record-web",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--receipt",
            str(browser_receipt),
            "--json",
        )

        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        record_payload = json.loads(recorded.stdout)
        self.assertEqual(record_payload["artifacts_copied"], 2)
        durable_receipt = json.loads(
            Path(record_payload["receipt"]).read_text(encoding="utf-8")
        )
        self.assertEqual(
            durable_receipt["artifacts"][1]["copy"].split("/")[-1],
            "002-trace.zip",
        )
        copied_trace = (
            Path(str(initialized["state"])).parent
            / durable_receipt["artifacts"][1]["copy"]
        )
        copied_trace.write_bytes(b"tampered")

        status = self.cli("status", "--session", "demo", "--json")
        status_payload = json.loads(status.stdout)
        self.assertEqual(status_payload["effective_status"], "invalid-evidence")
        self.assertIn("hash", status_payload["criteria"][0]["evidence_error"])

    def test_desktop_evidence_copies_transcript_and_detects_tampering(self) -> None:
        initialized = self.initialize()
        run_root = self.base / "qcu-run"
        run_root.mkdir()
        transcript = run_root / "qcu-transcript.json"
        transcript.write_text('{"session":"desktop"}\n', encoding="utf-8")
        payload = {
            "schema_version": 1,
            "type": "qcu_desktop_evidence",
            "provider": "quick-computer-use",
            "qcuVersion": "0.1.0",
            "sessionId": "desktop",
            "objective": "Write a note",
            "expectedWindow": "Notepad",
            "realActions": True,
            "objectiveSatisfied": True,
            "focusVerified": True,
            "finished": True,
            "finishReason": "objective_verified",
            "verification": {"evidence": "The note is visible."},
            "summary": {
                "actionCount": 1,
                "foregroundObservationCount": 1,
                "staleFrameCount": 0,
                "busyNoQueueCount": 0,
            },
            "artifactRoot": str(run_root),
            "artifacts": [
                {
                    "path": transcript.name,
                    "sha256": work_state._sha256_file(transcript),
                    "bytes": transcript.stat().st_size,
                }
            ],
        }
        receipt = run_root / "cognitive-qcu-receipt.json"
        receipt.write_text(json.dumps(payload), encoding="utf-8")

        recorded = self.cli(
            "record-desktop",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--receipt",
            str(receipt),
            "--json",
        )

        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        result = json.loads(recorded.stdout)
        self.assertEqual(result["artifacts_copied"], 1)
        durable = json.loads(Path(result["receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(durable["type"], "desktop_evidence")
        copied = (
            Path(str(initialized["state"])).parent / durable["artifacts"][0]["copy"]
        )
        copied.write_text("tampered\n", encoding="utf-8")

        status = self.cli("status", "--session", "demo", "--json")
        status_payload = json.loads(status.stdout)
        self.assertEqual(status_payload["effective_status"], "invalid-evidence")
        self.assertIn("hash", status_payload["criteria"][0]["evidence_error"])

    def test_desktop_evidence_rejects_primitive_success_without_objective_completion(
        self,
    ) -> None:
        self.initialize()
        run_root = self.base / "qcu-incomplete"
        run_root.mkdir()
        transcript = run_root / "qcu-transcript.json"
        transcript.write_text("{}\n", encoding="utf-8")
        receipt = run_root / "cognitive-qcu-receipt.json"
        receipt.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "type": "qcu_desktop_evidence",
                    "provider": "quick-computer-use",
                    "realActions": True,
                    "objectiveSatisfied": False,
                    "focusVerified": True,
                    "finished": True,
                    "finishReason": "objective_verified",
                    "summary": {
                        "actionCount": 1,
                        "staleFrameCount": 0,
                        "busyNoQueueCount": 0,
                    },
                    "artifactRoot": str(run_root),
                    "artifacts": [
                        {
                            "path": transcript.name,
                            "sha256": work_state._sha256_file(transcript),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        recorded = self.cli(
            "record-desktop",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--receipt",
            str(receipt),
            "--json",
        )
        self.assertEqual(recorded.returncode, 2)
        self.assertIn("verified QCU completion", recorded.stdout)

    def test_navigation_evidence_remains_typed_and_detects_tampering(self) -> None:
        self.initialize()
        run_root = self.base / "skyvern-run"
        run_root.mkdir()
        raw_response = run_root / "run-final.json"
        raw_response.write_text(
            json.dumps({"run_id": "tsk_fixture", "status": "completed"}),
            encoding="utf-8",
        )
        payload = {
            "schema_version": 1,
            "type": "skyvern_navigation_evidence",
            "provider": "skyvern",
            "navigationOnly": True,
            "verificationEligible": False,
            "final": True,
            "discoveryCompleted": True,
            "runId": "tsk_fixture",
            "status": "completed",
            "sideEffectScope": "observe",
            "artifactRoot": str(run_root),
            "artifacts": [
                {
                    "path": "run-final.json",
                    "sha256": work_state._sha256_file(raw_response),
                    "bytes": raw_response.stat().st_size,
                }
            ],
        }
        navigation_receipt = run_root / "cognitive-skyvern-receipt.json"
        navigation_receipt.write_text(json.dumps(payload), encoding="utf-8")

        recorded = self.cli(
            "record-navigation",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--executor",
            "explorer",
            "--receipt",
            str(navigation_receipt),
            "--json",
        )

        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        recorded_payload = json.loads(recorded.stdout)
        self.assertTrue(recorded_payload["navigation_only"])
        durable = json.loads(
            Path(recorded_payload["receipt"]).read_text(encoding="utf-8")
        )
        self.assertFalse(durable["verification_eligible"])
        copied_artifact = (
            Path(recorded_payload["receipt"]).parents[3]
            / durable["artifacts"][0]["copy"]
        )
        copied_artifact.write_text("tampered", encoding="utf-8")

        status = self.cli("status", "--session", "demo", "--json")
        status_payload = json.loads(status.stdout)
        self.assertEqual(status_payload["effective_status"], "invalid-evidence")
        self.assertIn("hash", status_payload["criteria"][0]["evidence_error"])

    def test_design_evidence_remains_non_behavioral_and_detects_tampering(self) -> None:
        self.initialize("design")
        run_root = self.base / "design-run"
        run_root.mkdir()
        artifacts = []
        for name, content in (
            ("design-intent.json", b'{"type":"design_intent"}'),
            ("playwright-receipt.json", b'{"passed":true}'),
            ("visual-review.json", b'{"reviewer":"reviewer"}'),
            ("mobile.png", b"mobile render"),
            ("desktop.png", b"desktop render"),
        ):
            path = run_root / name
            path.write_bytes(content)
            artifacts.append(
                {
                    "kind": name,
                    "copy": str(path),
                    "sha256": work_state._sha256_file(path),
                }
            )
        payload = {
            "type": "visual_design_evidence",
            "schemaVersion": 1,
            "artifactRoot": str(run_root),
            "reviewer": "visual-reviewer",
            "intentIdentity": "a" * 64,
            "visualContractPassed": True,
            "behavioralVerificationEligible": False,
            "subjectiveQualityProven": False,
            "mobileCaptured": True,
            "desktopCaptured": True,
            "browserPassed": True,
            "artifacts": artifacts,
        }
        design_receipt = run_root / "cognitive-design-receipt.json"
        design_receipt.write_text(json.dumps(payload), encoding="utf-8")
        recorded = self.cli(
            "record-design",
            "--session",
            "design",
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--receipt",
            str(design_receipt),
            "--json",
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        record_payload = json.loads(recorded.stdout)
        self.assertFalse(record_payload["behavioral_verification_eligible"])
        durable = json.loads(
            Path(record_payload["receipt"]).read_text(encoding="utf-8")
        )
        self.assertEqual(durable["type"], "design_evidence")
        self.assertFalse(durable["subjective_quality_proven"])
        copied_artifact = (
            Path(record_payload["receipt"]).parents[3] / durable["artifacts"][0]["copy"]
        )
        copied_artifact.write_text("tampered", encoding="utf-8")
        status = self.cli("status", "--session", "design", "--json")
        status_payload = json.loads(status.stdout)
        self.assertEqual(status_payload["effective_status"], "invalid-evidence")
        self.assertIn("hash", status_payload["criteria"][0]["evidence_error"])

    def test_failed_command_is_recorded_and_blocks_completion(self) -> None:
        initialized = self.initialize()
        session_dir = Path(str(initialized["state"])).parent

        failed = self.cli(
            "run",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--json",
            "--",
            sys.executable,
            "-c",
            "import sys; print('real failure'); sys.exit(7)",
        )
        self.assertEqual(failed.returncode, 7)
        payload = json.loads(failed.stdout)
        self.assertEqual(payload["status"], "failed")
        receipt = json.loads(Path(payload["receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(receipt["exit_code"], 7)
        self.assertIn("real failure", receipt["stdout_tail"])

        completed = self.cli("complete", "--session", "demo", "--json")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("c1:failed", completed.stdout)

        status = self.cli("status", "--session", "demo", "--json")
        self.assertEqual(status.returncode, 0, status.stderr)
        status_payload = json.loads(status.stdout)
        self.assertEqual(status_payload["effective_status"], "active")
        self.assertFalse(status_payload["criteria"][0]["stale"])
        self.assertIsNone(status_payload["criteria"][0]["evidence_valid"])
        self.assertFalse(any(session_dir.glob(".*.tmp")))

    def test_red_green_cycle_binds_failure_and_success_to_changed_source(self) -> None:
        initialized = self.initialize()
        session_dir = Path(str(initialized["state"])).parent
        command = [
            sys.executable,
            "-c",
            "from pathlib import Path; assert 'VALUE = 2' in Path('source.py').read_text()",
        ]

        red = self.cli(
            "run-red",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--json",
            "--",
            *command,
        )
        self.assertEqual(red.returncode, 0, red.stderr)
        red_payload = json.loads(red.stdout)
        self.assertEqual(red_payload["status"], "red")
        red_receipt = json.loads(
            Path(red_payload["receipt"]).read_text(encoding="utf-8")
        )
        self.assertNotEqual(red_receipt["exit_code"], 0)

        unchanged = self.cli(
            "run-green",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--json",
            "--",
            *command,
        )
        self.assertEqual(unchanged.returncode, 2)
        self.assertIn("source must change", unchanged.stdout)

        (self.workspace / "source.py").write_text("VALUE = 2\n", encoding="utf-8")
        green = self.cli(
            "run-green",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--json",
            "--",
            *command,
        )
        self.assertEqual(green.returncode, 0, green.stderr)
        green_payload = json.loads(green.stdout)
        self.assertEqual(green_payload["status"], "claimed")
        cycle = json.loads(Path(green_payload["receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(cycle["type"], "test_cycle")
        self.assertEqual(cycle["command"], red_receipt["command"])
        self.assertNotEqual(
            cycle["source_fingerprint"]["sha256"],
            red_receipt["source_fingerprint"]["sha256"],
        )

        verified = self.cli(
            "verify",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--verifier",
            "reviewer",
            "--verdict",
            "confirmed",
            "--note",
            "The same regression command failed before the source change and passed after it",
            "--json",
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        completed = self.cli("complete", "--session", "demo", "--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)

        events = [
            json.loads(line)["event"]
            for line in (session_dir / "ledger.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertIn("red_demonstrated", events)
        self.assertIn("test_cycle_claimed", events)

    def test_red_phase_rejects_a_test_that_already_passes(self) -> None:
        self.initialize()
        red = self.cli(
            "run-red",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--json",
            "--",
            sys.executable,
            "-c",
            "print('already green')",
        )
        self.assertEqual(red.returncode, 2)
        payload = json.loads(red.stdout)
        self.assertEqual(payload["status"], "failed")
        self.assertIn("unexpectedly passed", payload["message"])

    def test_red_phase_rejects_a_command_that_cannot_start(self) -> None:
        self.initialize()
        red = self.cli(
            "run-red",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--json",
            "--",
            "executable-that-does-not-exist-cognitive-powers",
        )
        self.assertEqual(red.returncode, 2)
        payload = json.loads(red.stdout)
        self.assertEqual(payload["status"], "failed")
        self.assertIn("did not start", payload["message"])
        receipt = json.loads(Path(payload["receipt"]).read_text(encoding="utf-8"))
        self.assertFalse(receipt["command_started"])

    def test_green_phase_rejects_a_different_command(self) -> None:
        self.initialize()
        red = self.cli(
            "run-red",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--json",
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(1)",
        )
        self.assertEqual(red.returncode, 0, red.stderr)
        (self.workspace / "source.py").write_text("VALUE = 2\n", encoding="utf-8")
        green = self.cli(
            "run-green",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--json",
            "--",
            sys.executable,
            "-c",
            "print('different command')",
        )
        self.assertEqual(green.returncode, 2)
        self.assertIn("exact run-red command", green.stdout)

    def test_green_phase_rejects_tampered_red_evidence(self) -> None:
        initialized = self.initialize()
        red = self.cli(
            "run-red",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--json",
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(1)",
        )
        self.assertEqual(red.returncode, 0, red.stderr)
        red_path = Path(json.loads(red.stdout)["receipt"])
        receipt = json.loads(red_path.read_text(encoding="utf-8"))
        receipt["exit_code"] = 9
        red_path.write_text(json.dumps(receipt), encoding="utf-8")
        (self.workspace / "source.py").write_text("VALUE = 2\n", encoding="utf-8")

        green = self.cli(
            "run-green",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--json",
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(1)",
        )
        self.assertEqual(green.returncode, 2)
        self.assertIn("no longer matches durable state", green.stdout)

        session_dir = Path(str(initialized["state"])).parent
        status = self.cli("status", "--session", "demo", "--json")
        self.assertEqual(status.returncode, 0, status.stderr)
        status_payload = json.loads(status.stdout)
        self.assertEqual(status_payload["effective_status"], "invalid-evidence")
        self.assertFalse(status_payload["criteria"][0]["red_evidence_valid"])
        self.assertIn(
            "no longer matches durable state",
            status_payload["criteria"][0]["red_evidence_error"],
        )
        self.assertEqual(session_dir, Path(status_payload["session_dir"]))

    def test_source_change_makes_claim_stale(self) -> None:
        self.initialize()
        claimed = self.claim_with_passing_command()
        self.assertEqual(claimed.returncode, 0, claimed.stderr)
        (self.workspace / "source.py").write_text("VALUE = 2\n", encoding="utf-8")

        verified = self.cli(
            "verify",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--verifier",
            "reviewer",
            "--verdict",
            "confirmed",
            "--note",
            "Independent rerun",
            "--json",
        )
        self.assertEqual(verified.returncode, 2)
        self.assertIn("evidence is stale", verified.stdout)

        status = self.cli("status", "--session", "demo", "--json")
        self.assertEqual(status.returncode, 0, status.stderr)
        payload = json.loads(status.stdout)
        self.assertEqual(payload["effective_status"], "stale")
        self.assertTrue(payload["criteria"][0]["stale"])

    def test_codegraph_indexes_do_not_change_source_fingerprint(self) -> None:
        before = work_state.source_fingerprint(self.workspace, self.data_root)
        index = self.workspace / ".codegraph"
        alternate_index = self.workspace / ".codegraph-win"
        index.mkdir()
        alternate_index.mkdir()
        (index / "codegraph.db").write_bytes(b"first index")
        (alternate_index / "codegraph.db").write_bytes(b"second index")

        after = work_state.source_fingerprint(self.workspace, self.data_root)

        self.assertEqual(after, before)

    def test_nonempty_but_malformed_receipt_is_rejected(self) -> None:
        initialized = self.initialize()
        claimed = self.claim_with_passing_command()
        self.assertEqual(claimed.returncode, 0, claimed.stderr)
        session_dir = Path(str(initialized["state"])).parent
        state = json.loads((session_dir / "state.json").read_text(encoding="utf-8"))
        receipt = session_dir / state["criteria"][0]["receipt"]
        receipt.write_text('{"nonempty": true}\n', encoding="utf-8")

        verified = self.cli(
            "verify",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--verifier",
            "reviewer",
            "--verdict",
            "confirmed",
            "--note",
            "This should not be accepted",
            "--json",
        )
        self.assertEqual(verified.returncode, 2)
        self.assertIn("missing fields", verified.stdout)

        status = self.cli("status", "--session", "demo", "--json")
        self.assertEqual(status.returncode, 0, status.stderr)
        status_payload = json.loads(status.stdout)
        self.assertEqual(status_payload["effective_status"], "invalid-evidence")
        self.assertFalse(status_payload["criteria"][0]["stale"])
        self.assertFalse(status_payload["criteria"][0]["evidence_valid"])

    def test_evidence_changed_after_verification_blocks_completion(self) -> None:
        initialized = self.initialize()
        claimed = self.claim_with_passing_command()
        self.assertEqual(claimed.returncode, 0, claimed.stderr)
        verified = self.cli(
            "verify",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--verifier",
            "reviewer",
            "--verdict",
            "confirmed",
            "--note",
            "Evidence checked before tampering",
            "--json",
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)

        session_dir = Path(str(initialized["state"])).parent
        state = json.loads((session_dir / "state.json").read_text(encoding="utf-8"))
        receipt_path = session_dir / state["criteria"][0]["receipt"]
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["stdout_tail"] = "altered after review"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

        completed = self.cli("complete", "--session", "demo", "--json")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("evidence changed after verification", completed.stdout)

    def test_tampered_artifact_copy_is_rejected(self) -> None:
        self.initialize()
        artifact = self.workspace / "manual-check.txt"
        artifact.write_text("observed window behavior\n", encoding="utf-8")
        recorded = self.cli(
            "record",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--executor",
            "builder",
            "--artifact",
            str(artifact),
            "--summary",
            "Manual runtime observation",
            "--json",
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        copied = Path(json.loads(recorded.stdout)["artifact_copy"])
        copied.write_text("tampered\n", encoding="utf-8")

        verified = self.cli(
            "verify",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--verifier",
            "reviewer",
            "--verdict",
            "confirmed",
            "--note",
            "Artifact inspected",
            "--json",
        )
        self.assertEqual(verified.returncode, 2)
        self.assertIn("hash no longer matches", verified.stdout)

    def test_external_context_is_bound_and_expires(self) -> None:
        self.initialize()
        context = self.workspace / "context.json"
        context.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "type": "external_context",
                    "provider": "context7",
                    "library": "react",
                    "selected_library": {
                        "id": "/facebook/react/v19.1.2",
                        "matched_version": "19.1.2",
                    },
                    "requested_version": "19.1.2",
                    "query": "Effect cleanup semantics",
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "expires_at": (
                        datetime.now(timezone.utc) + timedelta(hours=1)
                    ).isoformat(),
                    "provider_response_sha256": "a" * 64,
                    "snippets": [{"kind": "info", "content": "Current docs"}],
                }
            ),
            encoding="utf-8",
        )

        recorded = self.cli(
            "record-context",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--executor",
            "researcher",
            "--artifact",
            str(context),
            "--summary",
            "Version-specific documentation",
            "--json",
        )
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        payload = json.loads(recorded.stdout)
        receipt_path = Path(payload["receipt"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["type"], "external_context")
        self.assertEqual(receipt["selected_library_id"], "/facebook/react/v19.1.2")

        receipt["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        status = self.cli("status", "--session", "demo", "--json")
        self.assertEqual(status.returncode, 0, status.stderr)
        status_payload = json.loads(status.stdout)
        self.assertEqual(status_payload["effective_status"], "stale")
        self.assertIn(
            "external context has expired",
            status_payload["criteria"][0]["evidence_error"],
        )

    def test_record_context_rejects_already_expired_payload(self) -> None:
        self.initialize()
        context = self.workspace / "expired.json"
        context.write_text(
            json.dumps(
                {
                    "type": "external_context",
                    "provider": "context7",
                    "library": "react",
                    "selected_library": {"id": "/facebook/react"},
                    "query": "hooks",
                    "expires_at": "2000-01-01T00:00:00Z",
                    "provider_response_sha256": "b" * 64,
                }
            ),
            encoding="utf-8",
        )

        recorded = self.cli(
            "record-context",
            "--session",
            "demo",
            "--criterion",
            "c1",
            "--executor",
            "researcher",
            "--artifact",
            str(context),
            "--summary",
            "Expired documentation",
            "--json",
        )
        self.assertEqual(recorded.returncode, 2)
        self.assertIn("already expired", recorded.stdout)

    def test_duplicate_initialization_preserves_existing_state(self) -> None:
        initialized = self.initialize()
        state_path = Path(str(initialized["state"]))
        original = state_path.read_bytes()

        duplicate = self.cli(
            "init",
            "--session",
            "demo",
            "--objective",
            "Overwrite it",
            "--criterion",
            "Different criterion",
            "--json",
        )
        self.assertEqual(duplicate.returncode, 2)
        self.assertIn("will not be overwritten", duplicate.stdout)
        self.assertEqual(state_path.read_bytes(), original)

    def test_data_root_inside_workspace_is_rejected_without_residue(self) -> None:
        internal_data = self.workspace / ".private-state"
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--root",
                str(self.workspace),
                "--data-root",
                str(internal_data),
                "init",
                "--session",
                "inside",
                "--objective",
                "Do not write state into the repository",
                "--criterion",
                "State remains external",
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("must be outside the workspace", completed.stdout)
        self.assertFalse(internal_data.exists())

    def test_state_migration_entrypoint_is_read_only_and_current_by_default(
        self,
    ) -> None:
        initialized = self.initialize()
        session_dir = Path(str(initialized["state"])).parent
        before = {
            path.relative_to(session_dir).as_posix(): path.read_bytes()
            for path in session_dir.rglob("*")
            if path.is_file()
        }

        completed = self.cli(
            "state-migrate",
            "--session",
            "demo",
            "--json",
        )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["policy_schema_version"], 1)
        self.assertEqual(report["mode"], "dry-run")
        self.assertEqual(report["state_schema_version"], 1)
        self.assertEqual(report["target_schema_version"], 1)
        self.assertFalse(report["migration_required"])
        self.assertEqual(report["status"], "current")
        self.assertFalse(report["backup_created"])
        after = {
            path.relative_to(session_dir).as_posix(): path.read_bytes()
            for path in session_dir.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)

    def test_state_migration_entrypoint_fails_closed_on_corrupt_ledger(self) -> None:
        initialized = self.initialize()
        session_dir = Path(str(initialized["state"])).parent
        with (session_dir / "ledger.jsonl").open("a", encoding="utf-8") as stream:
            stream.write("{interrupted\n")

        completed = self.cli(
            "state-migrate",
            "--session",
            "demo",
            "--json",
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("ledger line 2 is malformed", completed.stdout)

    def test_state_migration_policy_fails_closed_on_unknown_versions(self) -> None:
        for version in (True, "1", 0, 2):
            with self.subTest(version=version):
                session_dir = self.base / f"schema-{type(version).__name__}-{version}"
                session_dir.mkdir()
                (session_dir / "state.json").write_text(
                    json.dumps(
                        {
                            "schema_version": version,
                            "last_seq": 0,
                            "criteria": [],
                        }
                    ),
                    encoding="utf-8",
                )
                with self.assertRaises(work_state.WorkStateError):
                    work_state.state_migration_report(session_dir)

        malformed = self.base / "schema-current-malformed"
        malformed.mkdir()
        (malformed / "state.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "last_seq": "invalid",
                    "criteria": [],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(work_state.WorkStateError, "last_seq"):
            work_state.state_migration_report(malformed)

    def test_abandoned_lock_is_reclaimed(self) -> None:
        session_dir = self.base / "lock-session"
        session_dir.mkdir()
        lock_path = session_dir / ".state.lock"
        lock_path.write_text("999999 old\n", encoding="utf-8")
        old = work_state.time.time() - work_state.LOCK_STALE_SECONDS - 1
        work_state.os.utime(lock_path, (old, old))

        with work_state.session_lock(session_dir):
            self.assertTrue(lock_path.is_file())

        self.assertFalse(lock_path.exists())

    def test_stale_malformed_lock_is_reclaimed(self) -> None:
        session_dir = self.base / "malformed-lock-session"
        session_dir.mkdir()
        lock_path = session_dir / ".state.lock"
        lock_path.write_bytes(b"\xff\xfe")
        old = work_state.time.time() - work_state.LOCK_STALE_SECONDS - 1
        work_state.os.utime(lock_path, (old, old))

        with work_state.session_lock(session_dir):
            self.assertTrue(lock_path.is_file())

        self.assertFalse(lock_path.exists())

    def test_fresh_lock_from_dead_owner_is_reclaimed_immediately(self) -> None:
        session_dir = self.base / "dead-owner-lock-session"
        session_dir.mkdir()
        lock_path = session_dir / ".state.lock"
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        dead_pid = process.pid
        self.assertEqual(process.wait(timeout=5), 0)
        lock_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "pid": dead_pid,
                    "token": "abandoned-token",
                    "created_at": "2026-07-21T00:00:00Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        durability = work_state._DURABILITY_CORE._durability
        with mock.patch.object(durability, "LOCK_TIMEOUT_SECONDS", 0.15):
            with work_state.session_lock(session_dir):
                self.assertTrue(lock_path.is_file())
                self.assertNotEqual(
                    json.loads(lock_path.read_text(encoding="utf-8"))["token"],
                    "abandoned-token",
                )

        self.assertFalse(lock_path.exists())

    def test_live_lock_is_not_reclaimed_only_because_it_is_old(self) -> None:
        session_dir = self.base / "live-lock-session"
        session_dir.mkdir()
        lock_path = session_dir / ".state.lock"
        result: list[str] = []

        def contender() -> None:
            try:
                with work_state.session_lock(session_dir):
                    result.append("entered")
            except work_state.WorkStateError as error:
                result.append(str(error))

        durability = work_state._DURABILITY_CORE._durability
        with mock.patch.object(durability, "LOCK_TIMEOUT_SECONDS", 0.15):
            with work_state.session_lock(session_dir):
                old = work_state.time.time() - work_state.LOCK_STALE_SECONDS - 1
                work_state.os.utime(lock_path, (old, old))
                thread = threading.Thread(target=contender)
                thread.start()
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(len(result), 1)
                self.assertIn("timed out waiting for state lock", result[0])

        self.assertFalse(lock_path.exists())

    def test_lock_with_reused_pid_identity_is_reclaimed(self) -> None:
        session_dir = self.base / "reused-pid-lock-session"
        session_dir.mkdir()
        lock_path = session_dir / ".state.lock"
        lock_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "pid": os.getpid(),
                    "token": "former-owner",
                    "process_identity": "reused-pid-from-another-process",
                    "created_at": "2000-01-01T00:00:00Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        with work_state.session_lock(session_dir):
            current = json.loads(lock_path.read_text(encoding="utf-8"))
            self.assertNotEqual(current["token"], "former-owner")

        self.assertFalse(lock_path.exists())

    def test_live_lock_is_preserved_when_process_identity_is_temporarily_unreadable(
        self,
    ) -> None:
        session_dir = self.base / "unreadable-identity-lock-session"
        session_dir.mkdir()
        lock_path = session_dir / ".state.lock"
        original = {
            "schema_version": 1,
            "pid": os.getpid(),
            "token": "live-owner",
            "process_identity": "known-creation-identity",
            "created_at": "2000-01-01T00:00:00Z",
        }
        lock_path.write_text(json.dumps(original) + "\n", encoding="utf-8")

        durability = work_state._DURABILITY_CORE._durability
        with (
            mock.patch.object(durability, "LOCK_TIMEOUT_SECONDS", 0.15),
            mock.patch.object(durability, "_process_identity", return_value=None),
        ):
            with self.assertRaisesRegex(
                work_state.WorkStateError, "timed out waiting for state lock"
            ):
                with work_state.session_lock(session_dir):
                    self.fail("a live lock with unreadable identity was reclaimed")

        self.assertEqual(json.loads(lock_path.read_text(encoding="utf-8")), original)

    def test_lock_owner_only_removes_its_own_token(self) -> None:
        session_dir = self.base / "token-lock-session"
        session_dir.mkdir()
        lock_path = session_dir / ".state.lock"

        with work_state.session_lock(session_dir):
            replacement = {
                "schema_version": 1,
                "pid": 999999,
                "token": "replacement-token",
                "created_at": "2026-07-21T00:00:00Z",
            }
            lock_path.write_text(json.dumps(replacement) + "\n", encoding="utf-8")

        self.assertTrue(lock_path.is_file())
        self.assertEqual(
            json.loads(lock_path.read_text(encoding="utf-8"))["token"],
            "replacement-token",
        )

    def test_lock_generation_transitions_are_serialized_cross_process(self) -> None:
        session_dir = self.base / "serialized-lock-session"
        session_dir.mkdir()
        lock_path = session_dir / ".state.lock"
        release_marker = session_dir / "owner-released"
        result_path = session_dir / "successor-result"
        durability = work_state._DURABILITY_CORE._durability
        child_code = """
import pathlib
import runpy
import sys

module = runpy.run_path(sys.argv[1])
lock_path = pathlib.Path(sys.argv[2])
release_marker = pathlib.Path(sys.argv[3])
result_path = pathlib.Path(sys.argv[4])
print("attempting", flush=True)
with module["_state_lock_guard"](lock_path):
    result_path.write_text(
        "safe" if release_marker.exists() else "overlapped", encoding="utf-8"
    )
"""

        with durability._state_lock_guard(lock_path):
            successor = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    child_code,
                    str(Path(durability.__file__)),
                    str(lock_path),
                    str(release_marker),
                    str(result_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertIsNotNone(successor.stdout)
            self.assertEqual(successor.stdout.readline().strip(), "attempting")
            release_marker.write_text("released", encoding="utf-8")

        stdout, stderr = successor.communicate(timeout=5)
        self.assertEqual(successor.returncode, 0, stdout + stderr)
        self.assertEqual(result_path.read_text(encoding="utf-8"), "safe")

    def test_repeated_lock_acquisition_leaves_no_residual_owner_file(self) -> None:
        session_dir = self.base / "repeated-lock-session"
        for _ in range(32):
            with work_state.session_lock(session_dir):
                self.assertTrue((session_dir / ".state.lock").is_file())
            self.assertFalse((session_dir / ".state.lock").exists())

    def test_source_fingerprint_fails_when_a_source_file_is_unreadable(self) -> None:
        durability = work_state._DURABILITY_CORE._durability
        with mock.patch.object(
            durability, "_sha256_file", side_effect=OSError("read denied")
        ):
            with self.assertRaisesRegex(
                work_state.WorkStateError, "cannot fingerprint source file.*source.py"
            ):
                work_state.source_fingerprint(self.workspace, self.data_root)

    def test_source_fingerprint_fails_when_workspace_walk_is_incomplete(self) -> None:
        durability = work_state._DURABILITY_CORE._durability

        def denied_walk(root, *, followlinks, onerror):
            del root, followlinks
            onerror(PermissionError("walk denied"))
            return iter(())

        with mock.patch.object(durability.os, "walk", side_effect=denied_walk):
            with self.assertRaisesRegex(
                work_state.WorkStateError, "cannot enumerate workspace source"
            ):
                work_state.source_fingerprint(self.workspace, self.data_root)

    def test_corrupt_ledger_line_is_rejected_instead_of_skipped(self) -> None:
        session_dir = self.base / "corrupt-ledger-session"
        session_dir.mkdir()
        authenticated = work_state._encode_ledger_events(
            session_dir, [{"seq": 1, "event": "before"}]
        )
        (session_dir / "ledger.jsonl").write_text(
            authenticated + "{broken json}\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(work_state.WorkStateError, "line 2 is malformed"):
            work_state._read_ledger_events(session_dir)

    def test_ledger_snapshot_with_boolean_schema_is_rejected(self) -> None:
        session_dir = self.base / "boolean-schema-ledger-session"
        session_dir.mkdir()
        (session_dir / "state.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "last_seq": 0,
                    "criteria": [],
                }
            ),
            encoding="utf-8",
        )
        (session_dir / "ledger.jsonl").write_text(
            work_state._encode_ledger_events(
                session_dir,
                [
                    {
                        "seq": 1,
                        "event": "corrupt_snapshot",
                        "_state_snapshot": {
                            "schema_version": True,
                            "last_seq": 1,
                            "criteria": [],
                        },
                    }
                ],
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(work_state.WorkStateError, "ledger state snapshot"):
            work_state.load_state(session_dir)

    def test_malformed_state_sequence_fails_closed(self) -> None:
        session_dir = self.base / "malformed-sequence-session"
        session_dir.mkdir()
        (session_dir / "state.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "last_seq": "not-an-integer",
                    "criteria": [],
                    "work_packets": [],
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(work_state.WorkStateError, "last_seq"):
            work_state.load_state(session_dir)

    def test_write_ahead_ledger_recovers_newer_state_snapshot(self) -> None:
        initialized = self.initialize()
        session_dir = Path(str(initialized["state"])).parent
        state = work_state.load_state(session_dir)
        state["objective"] = "Recovered from ledger"

        with mock.patch.object(
            work_state,
            "_atomic_write_json",
            side_effect=OSError("snapshot interrupted"),
        ):
            with self.assertRaisesRegex(OSError, "snapshot interrupted"):
                work_state.save_state_with_event(
                    session_dir,
                    state,
                    "recovery_probe",
                )

        recovered = work_state.load_state(session_dir)
        self.assertEqual(recovered["objective"], "Recovered from ledger")
        self.assertEqual(recovered["last_seq"], 2)
        events = work_state._read_latest_events(session_dir)
        self.assertEqual(events[-1]["event"], "recovery_probe")
        self.assertNotIn("_state_snapshot", events[-1])

    def test_ledger_failure_does_not_advance_state_snapshot(self) -> None:
        initialized = self.initialize()
        session_dir = Path(str(initialized["state"])).parent
        state_path = session_dir / "state.json"
        before = state_path.read_bytes()
        state = work_state.load_state(session_dir)
        state["objective"] = "Must not commit"

        with mock.patch.object(
            work_state,
            "_append_ledger",
            side_effect=OSError("ledger unavailable"),
        ):
            with self.assertRaisesRegex(OSError, "ledger unavailable"):
                work_state.save_state_with_event(
                    session_dir,
                    state,
                    "uncommitted_probe",
                )

        self.assertEqual(state_path.read_bytes(), before)
        self.assertEqual(
            work_state.load_state(session_dir)["objective"],
            "Prove the requested behavior",
        )

    def test_atomic_write_failure_preserves_previous_file(self) -> None:
        target = self.base / "atomic.json"
        target.write_text("old\n", encoding="utf-8")

        with mock.patch.object(work_state.os, "replace", side_effect=OSError("stop")):
            with self.assertRaisesRegex(OSError, "stop"):
                work_state._atomic_write_text(target, "new\n")

        self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
        self.assertEqual(list(self.base.glob(".atomic.json.*.tmp")), [])

    def test_atomic_fsync_failure_preserves_previous_file(self) -> None:
        target = self.base / "atomic-fsync.json"
        target.write_text("old\n", encoding="utf-8")
        durability = work_state._DURABILITY_CORE._durability

        with mock.patch.object(
            durability.os, "fsync", side_effect=OSError("fsync interrupted")
        ):
            with self.assertRaisesRegex(OSError, "fsync interrupted"):
                work_state._atomic_write_text(target, "new\n")

        self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
        self.assertEqual(list(self.base.glob(".atomic-fsync.json.*.tmp")), [])

    def test_evidence_symlink_ancestor_cannot_escape_storage(self) -> None:
        session_dir = self.base / "symlink-evidence-session"
        evidence_root = session_dir / "evidence"
        outside = self.base / "outside-evidence"
        evidence_root.mkdir(parents=True)
        outside.mkdir()
        (outside / "receipt.json").write_text("{}", encoding="utf-8")
        link = evidence_root / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"directory symlinks unavailable: {error}")

        with self.assertRaisesRegex(work_state.WorkStateError, "escapes"):
            work_state._evidence_file_path(
                session_dir,
                "evidence/linked/receipt.json",
                "evidence receipt",
            )


if __name__ == "__main__":
    unittest.main()
