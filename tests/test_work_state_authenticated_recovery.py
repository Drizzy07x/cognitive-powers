from __future__ import annotations

import hashlib
import json
import secrets
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests.test_work_state import work_state


class WorkStateAuthenticatedRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "workspace"
        self.root.mkdir()
        self.data_root = self.base / "data"
        self.session = work_state.session_directory(
            self.root, self.data_root, "session"
        )
        self.session.mkdir(parents=True)
        self.state = {
            "schema_version": 1,
            "session_id": "s",
            "objective": "recover",
            "status": "active",
            "last_seq": 0,
            "criteria": [{"id": "c1", "status": "pending"}],
            "work_packets": [
                {"id": "done", "status": "completed", "dependencies": []},
                {"id": "next", "status": "planned", "dependencies": ["done"]},
            ],
        }
        work_state._atomic_write_json(self.session / "state.json", self.state)
        work_state.save_state_with_event(self.session, self.state, "checkpoint-ready")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_resume_summary_never_reschedules_completed_packets(self) -> None:
        summary = work_state.resume_summary(self.session)
        self.assertEqual(summary["completed_packet_ids"], ["done"])
        self.assertEqual(summary["runnable_packet_ids"], ["next"])
        self.assertEqual(summary["source"], "ledger")
        (self.session / "ledger.jsonl").write_text("broken\n", encoding="utf-8")
        with self.assertRaisesRegex(work_state.WorkStateError, "ledger"):
            work_state.resume_summary(self.session)

    def test_resume_rejects_semantically_tampered_authenticated_ledger(self) -> None:
        ledger = self.session / "ledger.jsonl"
        events = [
            json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()
        ]
        events[-1]["_state_checkpoint"]["work_packets"][1]["status"] = "completed"
        ledger.write_text(
            "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
        )
        with self.assertRaisesRegex(
            work_state.WorkStateError, "authentic|chain|digest"
        ):
            work_state.resume_summary(self.session)

    def test_compaction_verifies_bundle_and_real_write_boundaries(self) -> None:
        bundle = self.base / "bundle.zip"
        work_state.compact_session(self.session, bundle, retain_events=1)
        report = work_state.verify_compaction_bundle(bundle, self.session)
        self.assertTrue(report["verified"])
        faults = work_state.run_compaction_fault_injection(self.session, self.base)
        self.assertTrue(faults["passed"])
        self.assertGreaterEqual(len(faults["boundaries"]), 3)

    def test_a_bundle_resigned_under_a_new_key_is_refused(self) -> None:
        """Verifying with the key the bundle carries authenticates nothing.

        The old check required `.ledger.key` to be present *inside* the archive
        and verified against it, so an attacker could rewrite the state, re-sign
        the ledger under a key they chose, ship that key in the bundle, and be
        told `verified: True`. The fault injector never caught it because it
        only flipped one bit, which the in-bundle key still detects.
        """
        bundle = self.base / "bundle.zip"
        work_state.compact_session(self.session, bundle, retain_events=1)
        self.assertTrue(
            work_state.verify_compaction_bundle(bundle, self.session)["verified"]
        )

        forged = self.base / "forged.zip"
        forgery_source = self.base / "forgery"
        forgery_source.mkdir()
        with zipfile.ZipFile(bundle, "r") as archive:
            archive.extractall(forgery_source)
        state = json.loads((forgery_source / "state.json").read_text(encoding="utf-8"))
        state["objective"] = "an objective this session never had"
        (forgery_source / "state.json").write_text(json.dumps(state), encoding="utf-8")
        events = work_state._read_ledger_events(forgery_source)
        events[0]["_state_snapshot"] = state
        # Re-sign the whole ledger under a key of the forger's choosing and ship
        # that key with the archive. Reading has to happen first, under the real
        # key; swapping it earlier would only break the forger's own read.
        (forgery_source / ".ledger.key").write_text(
            secrets.token_bytes(32).hex() + "\n", encoding="ascii"
        )
        (forgery_source / "ledger.jsonl").write_text(
            work_state._encode_ledger_events(forgery_source, events), encoding="utf-8"
        )
        with zipfile.ZipFile(forged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(forgery_source.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(forgery_source).as_posix())

        # Self-consistent under its own key -- and that is precisely why the
        # in-bundle key cannot be the authority.
        self.assertTrue(work_state._read_ledger_events(forgery_source))
        with self.assertRaisesRegex(work_state.WorkStateError, "compaction_checkpoint"):
            work_state.verify_compaction_bundle(forged, self.session)

    def test_verify_bundle_is_reachable_from_the_command_line(self) -> None:
        bundle = self.base / "exports" / "cli.zip"
        work_state.compact_session(self.session, bundle, retain_events=1)
        arguments = work_state.build_parser().parse_args(
            [
                "--root",
                str(self.root),
                "--data-root",
                str(self.data_root),
                "verify-bundle",
                "--session",
                "session",
                "--bundle",
                str(bundle),
                "--json",
            ]
        )
        payload, exit_code = work_state.verify_bundle_command(arguments)
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["verified"])
        self.assertEqual(
            payload["bundle_sha256"],
            hashlib.sha256(bundle.read_bytes()).hexdigest(),
        )

    def test_bundle_compaction_retains_latest_verifiable_state(self) -> None:
        for index in range(8):
            work_state.save_state_with_event(self.session, self.state, f"event-{index}")
        bundle = self.base / "bundle.zip"
        report = work_state.compact_session(self.session, bundle, retain_events=3)
        self.assertTrue(bundle.is_file())
        self.assertEqual(
            hashlib.sha256(bundle.read_bytes()).hexdigest(), report["bundle_sha256"]
        )
        events = work_state._read_ledger_events(self.session)
        self.assertLessEqual(len(events), 4)
        self.assertEqual(events[0]["event"], "compaction_checkpoint")
        self.assertEqual(
            work_state.load_state(self.session)["last_seq"], self.state["last_seq"]
        )
        self.assertEqual(
            work_state.resume_summary(self.session)["completed_packet_ids"], ["done"]
        )

    def test_resume_and_bundle_compaction_are_supported_cli_surfaces(self) -> None:
        resume_args = work_state.build_parser().parse_args(
            [
                "--root",
                str(self.root),
                "--data-root",
                str(self.data_root),
                "resume-summary",
                "--session",
                "session",
                "--json",
            ]
        )
        resumed, resume_code = work_state.resume_session(resume_args)
        self.assertEqual(resume_code, 0)
        self.assertEqual(resumed["completed_packet_ids"], ["done"])

        bundle = self.base / "exports" / "session.zip"
        compact_args = work_state.build_parser().parse_args(
            [
                "--root",
                str(self.root),
                "--data-root",
                str(self.data_root),
                "compact",
                "--session",
                "session",
                "--bundle",
                str(bundle),
                "--retain-events",
                "1",
                "--json",
            ]
        )
        compacted, compact_code = work_state.compact_session_command(compact_args)
        self.assertEqual(compact_code, 0)
        self.assertTrue(compacted["last_verifiable_state_retained"])
        self.assertTrue(bundle.is_file())


if __name__ == "__main__":
    unittest.main()
