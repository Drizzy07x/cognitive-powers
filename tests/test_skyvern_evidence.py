from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT
    / "skills"
    / "explore-web-adaptively"
    / "scripts"
    / "skyvern_evidence.py"
)


def load_skyvern_evidence():
    spec = importlib.util.spec_from_file_location(
        "test_skyvern_evidence_module", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


skyvern_evidence = load_skyvern_evidence()


class FakeTransport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, bytes]:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "body": body,
                "timeout": timeout,
            }
        )
        value = self.responses.pop(0)
        return 200, json.dumps(value).encode("utf-8")


def completed_run(status: str = "completed") -> dict[str, object]:
    return {
        "run_id": "tsk_fixture_123",
        "status": status,
        "output": {"heading": "Retry control"},
        "step_count": 3,
        "run_type": "task_v1",
        "failure_reason": None,
        "errors": [],
        "screenshot_urls": ["https://example.invalid/screenshot.png"],
        "recording_url": "https://example.invalid/recording.mp4",
    }


class SkyvernEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_probe_is_explicitly_unusable_without_api_key(self) -> None:
        result = skyvern_evidence.probe(api_key=None)

        self.assertFalse(result["available"])
        self.assertFalse(result["usable"])
        self.assertIn("not configured", result["reason"])
        self.assertFalse(result["liveChecked"])

    def test_live_execution_requires_explicit_execute_and_submit_consent(self) -> None:
        with self.assertRaises(skyvern_evidence.SkyvernEvidenceError):
            skyvern_evidence.run_task(
                self.workspace,
                prompt="Inspect the retry flow",
                url="https://example.com",
                execute=False,
                api_key="secret",
            )
        with self.assertRaises(skyvern_evidence.SkyvernEvidenceError):
            skyvern_evidence.run_task(
                self.workspace,
                prompt="Submit the form",
                url="https://example.com",
                execute=True,
                side_effect_scope="submit",
                api_key="secret",
            )

    def test_completed_live_run_is_navigation_only_and_hashed(self) -> None:
        transport = FakeTransport(
            [
                {"run_id": "tsk_fixture_123", "status": "running"},
                completed_run(),
                [{"step": 1, "action": "inspect"}],
                [
                    {
                        "artifact_type": "screenshot",
                        "uri": "https://example.invalid/a.png",
                    }
                ],
            ]
        )
        artifact_dir = self.base / "skyvern-run"

        receipt, exit_code = skyvern_evidence.run_task(
            self.workspace,
            prompt="Find the retry control",
            url="https://example.com",
            execute=True,
            api_key="top-secret-value",
            max_steps=4,
            artifact_dir=artifact_dir,
            transport=transport,
            sleep=lambda _: None,
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(receipt["discoveryCompleted"])
        self.assertTrue(receipt["navigationOnly"])
        self.assertFalse(receipt["verificationEligible"])
        self.assertFalse(receipt["remoteArtifactsDownloaded"])
        self.assertEqual(receipt["stepCount"], 3)
        self.assertEqual(len(receipt["artifacts"]), 5)
        serialized = Path(receipt["receipt"]).read_text(encoding="utf-8")
        self.assertNotIn("top-secret-value", serialized)
        request_body = json.loads(transport.calls[0]["body"].decode("utf-8"))
        self.assertTrue(request_body["prompt"].startswith("OBSERVATION ONLY:"))
        self.assertEqual(
            transport.calls[0]["url"], "https://api.skyvern.com/v1/run/tasks"
        )

    def test_failed_provider_run_cannot_look_completed(self) -> None:
        failed = completed_run("failed")
        failed["failure_reason"] = "button not found"
        transport = FakeTransport([failed, [], []])

        receipt, exit_code = skyvern_evidence.run_task(
            self.workspace,
            prompt="Find the retry control",
            url="https://example.com",
            execute=True,
            api_key="secret",
            artifact_dir=self.base / "failed-run",
            transport=transport,
        )

        self.assertEqual(exit_code, 1)
        self.assertFalse(receipt["discoveryCompleted"])
        self.assertFalse(receipt["verificationEligible"])
        self.assertEqual(receipt["failureReason"], "button not found")

    def test_ingested_run_produces_fail_closed_playwright_handoff(self) -> None:
        response = self.base / "run.json"
        request = self.base / "request.json"
        response.write_text(json.dumps(completed_run()), encoding="utf-8")
        request.write_text(
            json.dumps(
                {"url": "https://example.com", "prompt": "Find the retry control"}
            ),
            encoding="utf-8",
        )
        receipt, exit_code = skyvern_evidence.ingest(
            self.workspace,
            response,
            request_path=request,
            artifact_dir=self.base / "ingested",
        )

        handoff = skyvern_evidence.handoff(
            self.workspace,
            receipt["receipt"],
            output_dir=self.base / "handoff",
        )

        self.assertEqual(exit_code, 0)
        self.assertFalse(handoff["verificationEligible"])
        self.assertTrue(handoff["failClosed"])
        candidate = Path(handoff["candidate"]).read_text(encoding="utf-8")
        self.assertIn("Skyvern discovery is not Playwright verification", candidate)
        self.assertIn("await page.goto", candidate)


if __name__ == "__main__":
    unittest.main()
