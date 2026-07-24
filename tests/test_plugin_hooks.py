from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN_ROOT / "hooks" / "selective_hooks.py"
WORK_STATE = PLUGIN_ROOT / "skills" / "execute-durably" / "scripts" / "work_state.py"


class PluginHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.repo = self.base / "repo"
        self.data = self.base / "plugin-data"
        self.repo.mkdir()
        self.env = os.environ.copy()
        self.env.update(
            {"PLUGIN_ROOT": str(PLUGIN_ROOT), "PLUGIN_DATA": str(self.data)}
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_hook(
        self, command: str, payload: object, env=None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), command],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env or self.env,
            check=False,
        )

    def payload(self, tool: str = "apply_patch") -> dict[str, object]:
        return {
            "sessionId": "session/with unsafe characters",
            "turnId": "turn-1",
            "cwd": str(self.repo),
            "toolName": tool,
            "toolInput": {"patch": "*** Update File: src/example.py\n@@\n-old\n+new"},
        }

    def ledger(self) -> Path:
        key = hashlib.sha256(b"session/with unsafe characters").hexdigest()[:32]
        return self.data / "hooks" / "events" / f"{key}.jsonl"

    def write_structural_evidence(
        self, path: Path, executor: str = "executor-1"
    ) -> None:
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "type": "command",
                    "criterion_id": "c1",
                    "executor": executor,
                    "command": ["python", "-m", "unittest"],
                    "exit_code": 0,
                    "stdout_sha256": "a" * 64,
                    "stderr_sha256": "b" * 64,
                    "source_fingerprint": {"sha256": "c" * 64},
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def create_verified_evidence(
        self,
        executor: str = "executor-1",
        verifier: str = "independent-reviewer",
    ) -> Path:
        def run_state(*arguments: str) -> subprocess.CompletedProcess[str]:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(WORK_STATE),
                    "--root",
                    str(self.repo),
                    "--data-root",
                    str(self.data),
                    *arguments,
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                completed.returncode, 0, completed.stdout + completed.stderr
            )
            return completed

        run_state(
            "init",
            "--session",
            "hook-evidence",
            "--objective",
            "Verify the hook fixture",
            "--criterion",
            "The focused command succeeds",
            "--json",
        )
        claimed = run_state(
            "run",
            "--session",
            "hook-evidence",
            "--criterion",
            "c1",
            "--executor",
            executor,
            "--json",
            "--",
            sys.executable,
            "-c",
            "print('verified')",
        )
        run_state(
            "verify",
            "--session",
            "hook-evidence",
            "--criterion",
            "c1",
            "--verifier",
            verifier,
            "--verdict",
            "confirmed",
            "--note",
            "The current command receipt proves the fixture criterion",
            "--json",
        )
        return Path(json.loads(claimed.stdout)["receipt"])

    def test_post_tool_use_reads_stdin_appends_and_hashes_changed_file(self) -> None:
        source = self.repo / "src" / "example.py"
        source.parent.mkdir()
        source.write_text("new\n", encoding="utf-8")

        first = self.run_hook("post-tool-use", self.payload())
        second_payload = self.payload("Write")
        second_payload["turnId"] = "turn-2"
        second_payload["toolInput"] = {"file_path": "src/example.py"}
        second = self.run_hook("post-tool-use", second_payload)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        events = [
            json.loads(line)
            for line in self.ledger().read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["files"][0]["path"], "src/example.py")
        self.assertEqual(
            events[0]["files"][0]["sha256"],
            hashlib.sha256(source.read_bytes()).hexdigest(),
        )
        self.assertEqual(events[1]["previousEventHash"], events[0]["eventHash"])

    def test_partial_or_invalid_stdin_never_blocks_and_writes_nothing(self) -> None:
        for raw in ("{", "[]", json.dumps({"sessionId": "x"})):
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "post-tool-use"],
                input=raw,
                text=True,
                capture_output=True,
                env=self.env,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
        self.assertFalse(self.data.exists())

    def test_data_path_inside_plugin_root_is_rejected(self) -> None:
        env = dict(self.env)
        env["PLUGIN_DATA"] = str(PLUGIN_ROOT / "hook-data-must-not-exist")
        result = self.run_hook("post-tool-use", self.payload(), env=env)
        self.assertEqual(result.returncode, 0)
        self.assertFalse((PLUGIN_ROOT / "hook-data-must-not-exist").exists())

    def test_paths_outside_cwd_are_never_hashed_or_recorded(self) -> None:
        outside = self.base / "secret.txt"
        outside.write_text("secret", encoding="utf-8")
        payload = self.payload("Write")
        payload["toolInput"] = {"file_path": str(outside)}
        self.run_hook("post-tool-use", payload)
        event = json.loads(self.ledger().read_text(encoding="utf-8"))
        self.assertEqual(event["files"], [])
        self.assertNotIn("secret", self.ledger().read_text(encoding="utf-8"))

    def test_stop_warns_for_edits_then_accepts_current_evidence_receipt(self) -> None:
        self.run_hook("post-tool-use", self.payload("Write"))
        stop = self.run_hook("stop", {"sessionId": "session/with unsafe characters"})
        self.assertIn("no current", json.loads(stop.stdout)["systemMessage"])

        evidence = self.create_verified_evidence()
        recorded = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "record-validation",
                "--session-id",
                "session/with unsafe characters",
                "--evidence",
                str(evidence),
                "--validator",
                "independent-reviewer",
            ],
            text=True,
            capture_output=True,
            env=self.env,
            check=False,
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        current = self.run_hook("stop", {"sessionId": "session/with unsafe characters"})
        self.assertEqual(current.stdout, "")

        evidence.write_text("tampered\n", encoding="utf-8")
        tampered = self.run_hook(
            "stop", {"sessionId": "session/with unsafe characters"}
        )
        self.assertIn("no current", json.loads(tampered.stdout)["systemMessage"])

    def test_garbage_or_self_validated_evidence_cannot_clear_warning(self) -> None:
        self.run_hook("post-tool-use", self.payload("Write"))
        evidence = self.base / "evidence.json"
        evidence.write_text('{"message":"looks good"}\n', encoding="utf-8")

        garbage = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "record-validation",
                "--session-id",
                "session/with unsafe characters",
                "--evidence",
                str(evidence),
                "--validator",
                "reviewer-1",
            ],
            text=True,
            capture_output=True,
            env=self.env,
            check=False,
        )
        self.assertEqual(garbage.returncode, 2)
        self.assertIn("schema_version 1", garbage.stderr)

        self.write_structural_evidence(evidence, executor="same-agent")
        self_validated = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "record-validation",
                "--session-id",
                "session/with unsafe characters",
                "--evidence",
                str(evidence),
                "--validator",
                "same-agent",
            ],
            text=True,
            capture_output=True,
            env=self.env,
            check=False,
        )
        self.assertEqual(self_validated.returncode, 2)
        self.assertIn("different from the executor", self_validated.stderr)
        warning = self.run_hook("stop", {"sessionId": "session/with unsafe characters"})
        self.assertIn("no current", json.loads(warning.stdout)["systemMessage"])

    def test_cognitive_data_override_is_used_when_plugin_data_is_absent(self) -> None:
        alternate = self.base / "cognitive-data"
        env = dict(self.env)
        env.pop("PLUGIN_DATA")
        env["COGNITIVE_POWERS_DATA"] = str(alternate)

        result = self.run_hook("post-tool-use", self.payload("Write"), env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list((alternate / "hooks" / "events").glob("*.jsonl"))), 1)

    def test_cognitive_data_override_precedes_plugin_data(self) -> None:
        preferred = self.base / "preferred-cognitive-data"
        env = dict(self.env)
        env["COGNITIVE_POWERS_DATA"] = str(preferred)

        result = self.run_hook("post-tool-use", self.payload("Write"), env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list((preferred / "hooks" / "events").glob("*.jsonl"))), 1)
        self.assertFalse((self.data / "hooks" / "events").exists())

    def test_structured_fake_receipt_without_durable_state_is_rejected(self) -> None:
        self.run_hook("post-tool-use", self.payload("Write"))
        evidence = (
            self.data
            / "projects"
            / "fake"
            / "sessions"
            / "fake"
            / "evidence"
            / "c1"
            / "attempt-1"
            / "receipt.json"
        )
        evidence.parent.mkdir(parents=True)
        self.write_structural_evidence(evidence, executor="executor-1")

        recorded = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "record-validation",
                "--session-id",
                "session/with unsafe characters",
                "--evidence",
                str(evidence),
                "--validator",
                "reviewer-1",
            ],
            text=True,
            capture_output=True,
            env=self.env,
            check=False,
        )

        self.assertEqual(recorded.returncode, 2)
        self.assertIn("session state is unreadable", recorded.stderr)
        warning = self.run_hook("stop", {"sessionId": "session/with unsafe characters"})
        self.assertIn("no current", json.loads(warning.stdout)["systemMessage"])

    def test_tampered_ledger_is_detected_and_never_silently_accepted(self) -> None:
        self.run_hook("post-tool-use", self.payload())
        event = json.loads(self.ledger().read_text(encoding="utf-8"))
        event["cwd"] = str(self.base / "different")
        self.ledger().write_text(json.dumps(event) + "\n", encoding="utf-8")

        result = self.run_hook("stop", {"sessionId": "session/with unsafe characters"})

        warning = json.loads(result.stdout)["systemMessage"]
        self.assertIn("hash changed", warning)

    def test_hash_chain_corruption_properties_fail_closed(self) -> None:
        for index in range(3):
            payload = self.payload()
            payload["turnId"] = f"turn-{index}"
            self.run_hook("post-tool-use", payload)
        original = self.ledger().read_text(encoding="utf-8").splitlines()

        def event_hash(event: dict[str, object]) -> str:
            unsigned = dict(event)
            unsigned.pop("eventHash", None)
            canonical = json.dumps(
                unsigned,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            return hashlib.sha256(canonical).hexdigest()

        def changed_hash(lines: list[str]) -> list[str]:
            events = [json.loads(line) for line in lines]
            events[1]["cwd"] = str(self.base / "tampered")
            return [json.dumps(event) for event in events]

        def broken_link(lines: list[str]) -> list[str]:
            events = [json.loads(line) for line in lines]
            events[1]["previousEventHash"] = "f" * 64
            events[1]["eventHash"] = event_hash(events[1])
            return [json.dumps(event) for event in events]

        def reordered(lines: list[str]) -> list[str]:
            return [lines[1], lines[0], *lines[2:]]

        cases = (
            ("hash changed", changed_hash(original), "hash changed"),
            ("chain link", broken_link(original), "breaks the hash chain"),
            ("reordered", reordered(original), "breaks the hash chain"),
            ("truncated", [*original[:1], '{"schema":'], "not JSON"),
        )
        for name, lines, expected in cases:
            with self.subTest(case=name):
                self.ledger().write_text("\n".join(lines) + "\n", encoding="utf-8")
                result = self.run_hook(
                    "stop", {"sessionId": "session/with unsafe characters"}
                )
                warning = json.loads(result.stdout)["systemMessage"]
                self.assertIn(expected, warning)

    def test_traversal_and_symlink_candidates_never_escape_cwd(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("do-not-record", encoding="utf-8")
        candidates = (
            "../outside/secret.txt",
            str(secret),
            "nested/../../outside/secret.txt",
        )
        for index, candidate in enumerate(candidates):
            payload = self.payload("Write")
            payload["turnId"] = f"traversal-{index}"
            payload["toolInput"] = {"file_path": candidate}
            result = self.run_hook("post-tool-use", payload)
            self.assertEqual(result.returncode, 0, result.stderr)

        link = self.repo / "linked-outside"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            link = None
        if link is not None:
            payload = self.payload("Write")
            payload["turnId"] = "symlink"
            payload["toolInput"] = {"file_path": "linked-outside/secret.txt"}
            result = self.run_hook("post-tool-use", payload)
            self.assertEqual(result.returncode, 0, result.stderr)

        events = [
            json.loads(line)
            for line in self.ledger().read_text(encoding="utf-8").splitlines()
        ]
        self.assertTrue(events)
        self.assertTrue(all(event["files"] == [] for event in events))
        self.assertNotIn(
            "do-not-record",
            self.ledger().read_text(encoding="utf-8"),
        )

    def test_short_lock_contention_does_not_silently_drop_event(self) -> None:
        lock = self.ledger().with_suffix(self.ledger().suffix + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import os, pathlib, sys, time\n"
                    "path = pathlib.Path(sys.argv[1])\n"
                    "handle = path.open('a+b')\n"
                    "handle.seek(0, os.SEEK_END)\n"
                    "if handle.tell() == 0:\n"
                    "    handle.write(b'\\0'); handle.flush()\n"
                    "handle.seek(0)\n"
                    "if os.name == 'nt':\n"
                    "    import msvcrt\n"
                    "    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)\n"
                    "else:\n"
                    "    import fcntl\n"
                    "    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)\n"
                    "print('ready', flush=True)\n"
                    "time.sleep(2.2)\n"
                    "if os.name == 'nt':\n"
                    "    handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)\n"
                    "else:\n"
                    "    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)\n"
                    "handle.close()\n"
                ),
                str(lock),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert holder.stdout is not None
        self.assertEqual(holder.stdout.readline().strip(), "ready")
        try:
            result = self.run_hook("post-tool-use", self.payload())
        finally:
            _stdout, stderr = holder.communicate(timeout=10)
        self.assertEqual(holder.returncode, 0, stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        events = self.ledger().read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(events), 1)

    def test_unlocked_residual_lock_file_does_not_block_event(self) -> None:
        lock = self.ledger().with_suffix(self.ledger().suffix + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("stale-owner\n", encoding="utf-8")

        result = self.run_hook("post-tool-use", self.payload())

        self.assertEqual(result.returncode, 0, result.stderr)
        events = self.ledger().read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(events), 1)

    def test_concurrent_events_preserve_one_valid_hash_chain(self) -> None:
        source = self.repo / "src" / "example.py"
        source.parent.mkdir()
        source.write_text("new\n", encoding="utf-8")
        payloads = []
        for index in range(8):
            payload = self.payload("Write")
            payload["turnId"] = f"turn-{index}"
            payload["toolInput"] = {"file_path": "src/example.py"}
            payloads.append(payload)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(
                pool.map(lambda item: self.run_hook("post-tool-use", item), payloads)
            )

        self.assertTrue(all(result.returncode == 0 for result in results))
        events = [
            json.loads(line)
            for line in self.ledger().read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len(events), 8)
        previous = None
        for event in events:
            self.assertEqual(event["previousEventHash"], previous)
            previous = event["eventHash"]


if __name__ == "__main__":
    unittest.main()
