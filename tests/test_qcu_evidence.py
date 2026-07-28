from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = (
    PLUGIN_ROOT
    / "skills"
    / "operate-desktop-adaptively"
    / "scripts"
    / "qcu_evidence.py"
)


def load_adapter():
    spec = importlib.util.spec_from_file_location(
        "test_qcu_evidence_module", ADAPTER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {ADAPTER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


qcu = load_adapter()


def valid_transcript() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "type": "qcu_desktop_transcript",
        "provider": "quick-computer-use",
        "qcuVersion": "0.1.0",
        "sessionId": "desktop-session",
        "objective": "Write and verify a note",
        "expectedWindow": "Notepad",
        "realActions": True,
        "commands": [
            {
                "name": "sidecar-ready",
                "argv": ["qcu", "sidecar-ready"],
                "exitCode": 0,
                "result": {"sidecar_status": "running"},
            },
            {
                "name": "sidecar-where",
                "argv": ["qcu", "sidecar-where", "--observe-profile", "fast"],
                "exitCode": 0,
                "result": {
                    "observation_id": "obs-start",
                    "focus": {
                        "expected": [{"target": "Notepad", "status": "foreground"}]
                    },
                    "capture": {"stale_frame_reused": False},
                },
            },
            {
                "name": "sidecar-do",
                "argv": ["qcu", "sidecar-do", "--brief"],
                "exitCode": 0,
                "result": {
                    "response": {
                        "status": "success",
                        "observation_id": "obs-final",
                        "after_observation": {"capture": {"stale_frame_reused": False}},
                    }
                },
            },
            {
                "name": "sidecar-where",
                "argv": ["qcu", "sidecar-where", "--observe-profile", "fast"],
                "exitCode": 0,
                "result": {
                    "observation_id": "obs-final",
                    "focus": {
                        "expected": [{"target": "Notepad", "status": "foreground"}]
                    },
                    "capture": {"stale_frame_reused": False},
                },
            },
            {
                "name": "finish",
                "argv": ["qcu", "finish", "--reason", "objective_verified"],
                "exitCode": 0,
                "result": {"status": "success", "reason": "objective_verified"},
            },
        ],
        "finalVerification": {
            "objectiveSatisfied": True,
            "expectedWindowForeground": True,
            "observationId": "obs-final",
            "evidence": "The requested note is visible in Notepad.",
        },
    }


class QcuEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_transcript(self, payload: dict[str, object]) -> Path:
        path = self.base / "transcript.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_probe_requires_the_complete_fast_surface(self) -> None:
        def runner(*args, **kwargs):
            return subprocess.CompletedProcess(
                args[0], 0, "sidecar-ready sidecar-where sidecar-do finish", ""
            )

        state = qcu.probe(self.workspace, executable=sys.executable, runner=runner)
        self.assertTrue(state["usable"])
        self.assertTrue(all(state["surface"].values()))

    def test_normalize_copies_and_hashes_a_valid_transcript(self) -> None:
        transcript = self.write_transcript(valid_transcript())
        receipt, exit_code = qcu.normalize(
            self.workspace, transcript, artifact_dir=self.base / "qcu-run"
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(receipt["objectiveSatisfied"])
        self.assertEqual(receipt["summary"]["actionCount"], 1)
        copied = Path(receipt["artifactRoot"]) / receipt["artifacts"][0]["path"]
        self.assertEqual(receipt["artifacts"][0]["sha256"], qcu._sha256_file(copied))

    def test_default_artifacts_are_partitioned_by_project(self) -> None:
        # browser and skyvern evidence already interpose the project key;
        # one shared qcu pool made per-project retention impossible.
        import os
        from unittest import mock

        transcript = self.write_transcript(valid_transcript())
        data_root = self.base / "qcu-data"
        with mock.patch.dict(os.environ, {"COGNITIVE_POWERS_DATA": str(data_root)}):
            receipt, exit_code = qcu.normalize(self.workspace, transcript)
        self.assertEqual(exit_code, 0)
        artifact_root = Path(receipt["artifactRoot"])
        expected_key = qcu._project_key(qcu.resolve_root(self.workspace))
        self.assertEqual(artifact_root.parent.parent.name, "qcu")
        self.assertEqual(artifact_root.parent.name, expected_key)

    def test_rejects_busy_input(self) -> None:
        payload = valid_transcript()
        payload["commands"][2]["result"]["response"]["status"] = "busy_no_queue"
        with self.assertRaisesRegex(qcu.QcuEvidenceError, "busy_no_queue"):
            qcu.normalize(
                self.workspace,
                self.write_transcript(payload),
                artifact_dir=self.base / "run",
            )

    def test_rejects_stale_capture(self) -> None:
        payload = valid_transcript()
        payload["commands"][1]["result"]["capture"]["stale_frame_reused"] = True
        with self.assertRaisesRegex(qcu.QcuEvidenceError, "stale"):
            qcu.normalize(
                self.workspace,
                self.write_transcript(payload),
                artifact_dir=self.base / "run",
            )

    def test_rejects_wrong_foreground_window(self) -> None:
        payload = valid_transcript()
        payload["commands"][1]["result"]["focus"]["expected"][0]["target"] = (
            "Calculator"
        )
        payload["commands"][3]["result"]["focus"]["expected"][0]["target"] = (
            "Calculator"
        )
        with self.assertRaisesRegex(qcu.QcuEvidenceError, "foreground"):
            qcu.normalize(
                self.workspace,
                self.write_transcript(payload),
                artifact_dir=self.base / "run",
            )

    def test_rejects_action_success_without_objective_success(self) -> None:
        payload = valid_transcript()
        payload["finalVerification"]["objectiveSatisfied"] = False
        with self.assertRaisesRegex(qcu.QcuEvidenceError, "objective and focus"):
            qcu.normalize(
                self.workspace,
                self.write_transcript(payload),
                artifact_dir=self.base / "run",
            )

    def test_rejects_sidecar_do_without_explicit_success(self) -> None:
        payload = valid_transcript()
        payload["commands"][2]["result"]["response"].pop("status")
        with self.assertRaisesRegex(qcu.QcuEvidenceError, "status success"):
            qcu.normalize(
                self.workspace,
                self.write_transcript(payload),
                artifact_dir=self.base / "run",
            )

    def test_rejects_final_observation_not_bound_to_post_action_focus(self) -> None:
        payload = valid_transcript()
        payload["commands"].pop(3)
        with self.assertRaisesRegex(qcu.QcuEvidenceError, "post-action foreground"):
            qcu.normalize(
                self.workspace,
                self.write_transcript(payload),
                artifact_dir=self.base / "run",
            )

    def test_rejects_artifacts_inside_workspace(self) -> None:
        with self.assertRaisesRegex(qcu.QcuEvidenceError, "outside the workspace"):
            qcu.normalize(
                self.workspace,
                self.write_transcript(valid_transcript()),
                artifact_dir=self.workspace / "qcu-run",
            )


if __name__ == "__main__":
    unittest.main()
