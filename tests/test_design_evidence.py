from __future__ import annotations

import importlib.util
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT / "skills" / "design-intentionally" / "scripts" / "design_evidence.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "test_design_evidence_module", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evidence = load_module()


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def write_png(path: Path, width: int, height: int) -> None:
    row = b"\x00" + b"\x20\x40\x60" * width
    raw = row * height
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(raw))
        + png_chunk(b"IEND", b"")
    )
    path.write_bytes(payload)


class DesignEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.external = self.root / "external"
        self.external.mkdir()
        self.intent = self.external / "intent.json"
        self.browser = self.external / "browser.json"
        self.browser_run = self.external / "browser-run"
        self.browser_run.mkdir()
        self.browser_report = self.browser_run / "report.json"
        self.browser_report.write_text('{"stats":{"expected":2}}', encoding="utf-8")
        self.review = self.external / "review.json"
        self.mobile = self.external / "mobile.png"
        self.desktop = self.external / "desktop.png"
        write_png(self.mobile, 375, 240)
        write_png(self.desktop, 1280, 240)
        intent_payload = {
            "type": "design_intent",
            "readyToImplement": True,
            "direction": ["calm"],
        }
        intent_payload["intentSha256"] = evidence.intent_identity(intent_payload)
        self.intent.write_text(json.dumps(intent_payload), encoding="utf-8")
        self.browser.write_text(
            json.dumps(
                {
                    "type": "playwright_evidence",
                    "passed": True,
                    "stats": {"expected": 2, "unexpected": 0},
                    "artifactRoot": str(self.browser_run),
                    "artifacts": [
                        {
                            "path": "report.json",
                            "sha256": evidence.sha256_file(self.browser_report),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def review_payload(self) -> dict[str, object]:
        return {
            "reviewer": "visual-reviewer",
            "viewports": [
                {
                    "name": "mobile",
                    "width": 375,
                    "height": 240,
                    "screenshot": str(self.mobile),
                },
                {
                    "name": "desktop",
                    "width": 1280,
                    "height": 240,
                    "screenshot": str(self.desktop),
                },
            ],
            "checks": [
                {"id": check, "verdict": "pass", "note": f"inspected {check}"}
                for check in sorted(evidence.REQUIRED_CHECKS)
            ],
        }

    def create(self, payload: dict[str, object], name: str = "run"):
        self.review.write_text(json.dumps(payload), encoding="utf-8")
        return evidence.create_evidence(
            self.workspace, self.intent, self.browser, self.review, self.external / name
        )

    def test_passing_review_copies_and_hashes_mobile_and_desktop_renders(self) -> None:
        receipt, exit_code = self.create(self.review_payload())
        self.assertEqual(exit_code, 0)
        self.assertTrue(receipt["visualContractPassed"])
        self.assertFalse(receipt["behavioralVerificationEligible"])
        self.assertFalse(receipt["subjectiveQualityProven"])
        self.assertEqual(len(receipt["viewports"]), 2)
        self.assertTrue(
            any(item["kind"] == "browser-artifact" for item in receipt["artifacts"])
        )
        self.assertTrue(
            all(Path(item["copy"]).is_file() for item in receipt["artifacts"])
        )

    def test_missing_desktop_render_cannot_pass(self) -> None:
        payload = self.review_payload()
        payload["viewports"] = payload["viewports"][:1]
        receipt, exit_code = self.create(payload)
        self.assertEqual(exit_code, 1)
        self.assertFalse(receipt["desktopCaptured"])
        self.assertFalse(receipt["visualContractPassed"])

    def test_declared_viewport_must_match_png_dimensions(self) -> None:
        payload = self.review_payload()
        payload["viewports"][0]["width"] = 390
        with self.assertRaises(evidence.EvidenceError):
            self.create(payload)

    def test_screenshot_inside_workspace_is_rejected(self) -> None:
        inside = self.workspace / "inside.png"
        write_png(inside, 375, 240)
        payload = self.review_payload()
        payload["viewports"][0]["screenshot"] = str(inside)
        with self.assertRaises(evidence.EvidenceError):
            self.create(payload)

    def test_tampered_intent_identity_is_rejected(self) -> None:
        intent_payload = json.loads(self.intent.read_text(encoding="utf-8"))
        intent_payload["direction"].append("changed")
        self.intent.write_text(json.dumps(intent_payload), encoding="utf-8")
        with self.assertRaises(evidence.EvidenceError):
            self.create(self.review_payload())

    def test_tampered_browser_artifact_is_rejected(self) -> None:
        self.browser_report.write_text("tampered", encoding="utf-8")
        with self.assertRaises(evidence.EvidenceError):
            self.create(self.review_payload())

    def test_screenshot_replaced_while_copying_is_rejected(self) -> None:
        """The preserved render must be the one whose dimensions were checked.

        Dimensions are read before the copy. Without a re-hash the receipt can
        claim a dimension-matched desktop render while preserving a different
        image, and the durable recorder trusts those declared hashes.
        """
        substitute = self.external / "substitute.png"
        write_png(substitute, 100, 100)
        replacement = substitute.read_bytes()
        real_copyfile = evidence.shutil.copyfile

        def racing_copyfile(source, target, **keywords):
            if Path(source).name == self.desktop.name:
                Path(target).write_bytes(replacement)
                return target
            return real_copyfile(source, target, **keywords)

        with mock.patch.object(evidence.shutil, "copyfile", racing_copyfile):
            with self.assertRaises(evidence.EvidenceError):
                self.create(self.review_payload())


if __name__ == "__main__":
    unittest.main()
