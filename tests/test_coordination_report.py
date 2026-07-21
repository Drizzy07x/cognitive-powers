from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
