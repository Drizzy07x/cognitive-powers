from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "skills" / "execute-durably" / "scripts" / "work_state.py"


def load_work_state():
    spec = importlib.util.spec_from_file_location(
        "test_work_state_recovery_module", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


work_state = load_work_state()


class WorkStateRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.session = self.base / "session"
        self.session.mkdir()
        self.state = {
            "schema_version": 1,
            "session_id": "recovery",
            "project_key": "project",
            "workspace_root": str(self.base),
            "objective": "recover",
            "status": "active",
            "created_at": work_state.utc_now(),
            "updated_at": work_state.utc_now(),
            "last_seq": 0,
            "work_packets": [],
            "criteria": [],
        }
        work_state.save_state_with_event(self.session, self.state, "initialized")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_non_checkpoint_events_use_deltas_and_latest_recovery_is_fixed_size(
        self,
    ) -> None:
        for index in range(1, 10):
            self.state["objective"] = f"transition-{index}"
            work_state.save_state_with_event(self.session, self.state, "transition")

        events = work_state._read_ledger_events(self.session)
        recovery = json.loads(
            (self.session / "recovery.json").read_text(encoding="utf-8")
        )

        self.assertIn("_state_checkpoint", events[0])
        self.assertTrue(all("_state_snapshot" not in event for event in events))
        self.assertTrue(all("_state_delta" in event for event in events[1:]))
        self.assertEqual(recovery["last_seq"], self.state["last_seq"])
        self.assertEqual(recovery["state"]["objective"], "transition-9")

    def test_periodic_checkpointing_is_bounded_between_delta_events(self) -> None:
        for index in range(1, work_state.LEDGER_CHECKPOINT_INTERVAL + 2):
            self.state["objective"] = f"transition-{index}"
            work_state.save_state_with_event(self.session, self.state, "transition")

        events = work_state._read_ledger_events(self.session)
        checkpoints = [event for event in events if "_state_checkpoint" in event]

        self.assertEqual(
            [event["seq"] for event in checkpoints],
            [1, work_state.LEDGER_CHECKPOINT_INTERVAL],
        )
        self.assertGreater(
            sum("_state_delta" in event for event in events), len(checkpoints)
        )

    def test_ledger_delta_recovers_latest_transition_if_both_snapshots_are_stale(
        self,
    ) -> None:
        stale_state = (self.session / "state.json").read_bytes()
        stale_recovery = (self.session / "recovery.json").read_bytes()
        self.state["objective"] = "latest flushed transition"

        with mock.patch.object(
            work_state, "_atomic_write_recovery", side_effect=OSError("interrupted")
        ):
            with self.assertRaisesRegex(OSError, "interrupted"):
                work_state.save_state_with_event(
                    self.session, self.state, "recovery_probe"
                )

        self.assertEqual((self.session / "state.json").read_bytes(), stale_state)
        self.assertEqual((self.session / "recovery.json").read_bytes(), stale_recovery)
        recovered = work_state.load_state(self.session)
        self.assertEqual(recovered["objective"], "latest flushed transition")
        self.assertEqual(recovered["last_seq"], 2)

    def test_recovery_checkpoint_recovers_when_state_replacement_is_interrupted(
        self,
    ) -> None:
        self.state["objective"] = "checkpoint survives"
        with mock.patch.object(
            work_state, "_atomic_write_json", side_effect=OSError("state interrupted")
        ):
            with self.assertRaisesRegex(OSError, "state interrupted"):
                work_state.save_state_with_event(
                    self.session, self.state, "checkpoint_probe"
                )

        recovered = work_state.load_state(self.session)
        self.assertEqual(recovered["objective"], "checkpoint survives")
        self.assertEqual(recovered["last_seq"], 2)

    def test_compaction_verifies_recovery_before_atomic_replacement(self) -> None:
        for index in range(1, 20):
            self.state["objective"] = f"transition-{index}"
            work_state.save_state_with_event(self.session, self.state, "transition")
        before = (self.session / "ledger.jsonl").read_bytes()

        with mock.patch.object(
            work_state._DURABILITY_CORE._durability.os,
            "replace",
            side_effect=OSError("replace interrupted"),
        ):
            with self.assertRaisesRegex(OSError, "replace interrupted"):
                work_state.compact_ledger(self.session)

        self.assertEqual((self.session / "ledger.jsonl").read_bytes(), before)
        report = work_state.compact_ledger(self.session)
        self.assertLess(report["events_after"], report["events_before"])
        self.assertTrue(report["recovery_verified"])
        self.assertEqual(
            work_state.load_state(self.session)["objective"], "transition-19"
        )

    def test_automatic_compaction_bounds_ledger_event_count(self) -> None:
        for index in range(work_state.LEDGER_MAX_EVENTS * 3):
            self.state["objective"] = f"bounded-{index}"
            work_state.save_state_with_event(self.session, self.state, "transition")

        events = work_state._read_ledger_events(self.session)
        self.assertLessEqual(len(events), work_state.LEDGER_MAX_EVENTS)
        self.assertEqual(
            work_state.load_state(self.session)["objective"],
            f"bounded-{work_state.LEDGER_MAX_EVENTS * 3 - 1}",
        )

    def test_corrupt_delta_and_checkpoint_fail_closed(self) -> None:
        self.state["objective"] = "new"
        work_state.save_state_with_event(self.session, self.state, "transition")
        ledger = self.session / "ledger.jsonl"
        lines = ledger.read_text(encoding="utf-8").splitlines()
        event = json.loads(lines[-1])
        objective = next(
            operation
            for operation in event["_state_delta"]
            if operation["path"] == ["objective"]
        )
        objective["value"] = "tampered"
        lines[-1] = json.dumps(event)
        ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with self.assertRaisesRegex(work_state.WorkStateError, "hash"):
            work_state.load_state(self.session)

    def test_compaction_refuses_corrupt_history_without_replacing_it(self) -> None:
        self.state["objective"] = "new"
        work_state.save_state_with_event(self.session, self.state, "transition")
        ledger = self.session / "ledger.jsonl"
        lines = ledger.read_text(encoding="utf-8").splitlines()
        event = json.loads(lines[-1])
        objective = next(
            operation
            for operation in event["_state_delta"]
            if operation["path"] == ["objective"]
        )
        objective["value"] = "corrupt"
        lines[-1] = json.dumps(event)
        corrupt = "\n".join(lines) + "\n"
        ledger.write_text(corrupt, encoding="utf-8")

        with self.assertRaisesRegex(work_state.WorkStateError, "hash"):
            work_state.compact_ledger(self.session)

        self.assertEqual(ledger.read_text(encoding="utf-8"), corrupt)

    def test_compact_cli_is_supported(self) -> None:
        args = work_state.build_parser().parse_args(
            [
                "--root",
                str(self.base),
                "--data-root",
                str(self.base.parent / "data"),
                "compact",
                "--session",
                "recovery",
            ]
        )
        self.assertEqual(args.subcommand, "compact")


if __name__ == "__main__":
    unittest.main()
