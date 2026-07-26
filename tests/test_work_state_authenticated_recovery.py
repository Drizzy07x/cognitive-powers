from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
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
        report = work_state.verify_compaction_bundle(bundle)
        self.assertTrue(report["verified"])
        faults = work_state.run_compaction_fault_injection(self.session, self.base)
        self.assertTrue(faults["passed"])
        self.assertGreaterEqual(len(faults["boundaries"]), 3)

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

    def test_fault_models_survive_1000_deterministic_sequences_each(self) -> None:
        report = work_state.run_fault_state_machines(seed=1600, sequences=1000)
        self.assertEqual(report["seed"], 1600)
        self.assertEqual(report["sequencesPerMachine"], 1000)
        self.assertEqual(
            set(report["machines"]),
            {"terminal-monotonicity", "dependency-resume", "wal-recovery"},
        )
        self.assertTrue(all(item["passed"] for item in report["machines"].values()))


if __name__ == "__main__":
    unittest.main()
