from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    PLUGIN_ROOT / "skills" / "execute-durably" / "scripts" / "coordination_report.py"
)
SPEC = importlib.util.spec_from_file_location("coordination_report", MODULE_PATH)
reporting = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(reporting)

# U+2028, U+2029, and U+0085: the separators str.splitlines() breaks on and
# "\n" does not. Spelled as code points so the fixture survives any editor or
# checkout that would otherwise rewrite them.
SPLITLINES_ONLY_SEPARATORS = (
    ("line", chr(0x2028)),
    ("paragraph", chr(0x2029)),
    ("next", chr(0x85)),
)


class CoordinationReportTests(unittest.TestCase):
    def test_report_derives_ready_waiting_and_blockers_from_one_state(self) -> None:
        state = {
            "session_id": "release-12",
            "status": "active",
            "objective": "ship",
            "last_seq": 4,
            "criteria": [
                {"id": "tests", "status": "verified"},
                {"id": "live", "status": "blocked", "reason": "provider unavailable"},
            ],
            "work_packets": [
                {"id": "core", "status": "complete", "depends_on": []},
                {"id": "docs", "status": "pending", "depends_on": ["core"]},
                {"id": "release", "status": "pending", "depends_on": ["docs"]},
            ],
        }
        events = [
            {
                "seq": 2,
                "at": "2026-01-02T00:00:00Z",
                "event": "packet_completed",
                "packet": "core",
                "_state_snapshot": {"large": "value"},
            },
            {
                "seq": 1,
                "at": "2026-01-01T00:00:00Z",
                "event": "packet_started",
                "packet": "core",
            },
        ]
        result = reporting.render_report(state, events)
        self.assertEqual(result["readyPackets"], ["docs"])
        self.assertEqual(
            result["waitingPackets"], [{"id": "release", "unmetDependencies": ["docs"]}]
        )
        self.assertEqual(result["blockers"][0]["id"], "live")
        self.assertEqual([item["seq"] for item in result["timeline"]], [1, 2])
        self.assertNotIn("_state_snapshot", result["timeline"][1])

    def test_malformed_packet_without_identity_fails(self) -> None:
        with self.assertRaises(reporting.ReportError):
            reporting.render_report(
                {"criteria": [], "work_packets": [{"status": "pending"}]}, []
            )

    def test_unicode_separators_inside_an_event_stay_one_record(self) -> None:
        # work_state writes the ledger with json.dumps(ensure_ascii=False), so an
        # objective carrying one of these separators reaches this reader raw.
        # splitlines() treated each of them as a record boundary, so the board,
        # timeline, blockers, and handoff were permanently unavailable for a
        # session work_state itself still read. Only "\n" terminates a record.
        for label, separator in SPLITLINES_ONLY_SEPARATORS:
            with (
                self.subTest(separator=label),
                tempfile.TemporaryDirectory() as temporary,
            ):
                ledger = Path(temporary) / "ledger.jsonl"
                objective = f"ship the {separator} release"
                ledger.write_text(
                    json.dumps(
                        {"seq": 1, "event": "session_initialized", "reason": objective},
                        ensure_ascii=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )

                events = reporting.read_events(ledger)

                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["reason"], objective)

    def test_interior_blank_ledger_line_is_still_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger = Path(temporary) / "ledger.jsonl"
            ledger.write_text(
                '{"seq": 1, "event": "session_initialized"}\n'
                "\n"
                '{"seq": 2, "event": "packet_started"}\n',
                encoding="utf-8",
            )

            events = reporting.read_events(ledger)

            self.assertEqual([item["seq"] for item in events], [1, 2])


if __name__ == "__main__":
    unittest.main()
