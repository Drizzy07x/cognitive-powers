#!/usr/bin/env python3
"""Bind design intent, browser evidence, review checks, and rendered screenshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_CHECKS = {
    "brief-fidelity",
    "hierarchy",
    "consistency",
    "responsive",
    "content-integrity",
}
VERDICTS = {"pass", "fail", "not-evaluated"}


class EvidenceError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def intent_identity(intent: dict[str, Any]) -> str:
    identity_payload = dict(intent)
    identity_payload.pop("intentSha256", None)
    return hashlib.sha256(canonical_json(identity_payload).encode("utf-8")).hexdigest()


def load_object(path: str | Path, label: str) -> tuple[Path, dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError(f"cannot read {label}: {source}") from error
    if not isinstance(payload, dict):
        raise EvidenceError(f"{label} must be a JSON object")
    return source, payload


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _copy(source: Path, output: Path, name: str) -> dict[str, Any]:
    target = output / name
    shutil.copyfile(source, target)
    return {
        "kind": name,
        "source": str(source),
        "copy": str(target),
        "sha256": sha256_file(target),
    }


def png_identity(path: Path) -> tuple[int, int, str]:
    """Return the dimensions and digest of one screenshot, read exactly once.

    Measuring and hashing in two passes over a mutable path can describe two
    different images: the receipt would then assert a dimension-matched render
    for bytes nothing checked, and the durable recorder trusts these declared
    values rather than re-reading the PNG. One handle is one image, whatever
    replaces the path afterwards.
    """
    digest = hashlib.sha256()
    with path.open("rb") as image:
        header = image.read(24)
        digest.update(header)
        if header[:8] != b"\x89PNG\r\n\x1a\n":
            raise EvidenceError(f"viewport screenshot must be PNG: {path}")
        if len(header) < 24:
            raise EvidenceError(f"viewport screenshot has no valid IHDR: {path}")
        length = struct.unpack(">I", header[8:12])[0]
        if header[12:16] != b"IHDR" or length < 8:
            raise EvidenceError(f"viewport screenshot has no valid IHDR: {path}")
        width, height = struct.unpack(">II", header[16:24])
        for chunk in iter(lambda: image.read(65536), b""):
            digest.update(chunk)
    return width, height, digest.hexdigest()


def create_evidence(
    workspace_root: str | Path,
    intent_path: str | Path,
    browser_path: str | Path,
    review_path: str | Path,
    artifact_dir: str | Path,
) -> tuple[dict[str, Any], int]:
    workspace = Path(workspace_root).expanduser().resolve()
    output = Path(artifact_dir).expanduser().resolve()
    if not workspace.is_dir():
        raise EvidenceError(f"workspace root is not a directory: {workspace}")
    if is_within(output, workspace):
        raise EvidenceError("design evidence must be stored outside the workspace")
    if output.exists() and any(output.iterdir()):
        raise EvidenceError(f"design artifact directory must be empty: {output}")
    intent_source, intent = load_object(intent_path, "design intent")
    browser_source, browser = load_object(browser_path, "browser receipt")
    review_source, review = load_object(review_path, "visual review")
    if (
        intent.get("type") != "design_intent"
        or intent.get("readyToImplement") is not True
    ):
        raise EvidenceError("design intent is unresolved or has the wrong type")
    if intent.get("intentSha256") != intent_identity(intent):
        raise EvidenceError("design intent identity does not match its contents")
    if (
        browser.get("type") != "playwright_evidence"
        or browser.get("passed") is not True
    ):
        raise EvidenceError(
            "browser receipt does not demonstrate a passing Playwright run"
        )
    stats = browser.get("stats")
    if (
        not isinstance(stats, dict)
        or int(stats.get("expected", 0)) < 1
        or int(stats.get("unexpected", 0)) != 0
    ):
        raise EvidenceError("browser receipt has no relevant passing test")
    browser_root_value = browser.get("artifactRoot")
    browser_artifacts_value = browser.get("artifacts")
    if (
        not isinstance(browser_root_value, str)
        or not browser_root_value
        or not isinstance(browser_artifacts_value, list)
        or not browser_artifacts_value
    ):
        raise EvidenceError("browser receipt has no artifact root or manifest")
    browser_root = Path(browser_root_value).expanduser().resolve()
    if not browser_root.is_dir() or is_within(browser_root, workspace):
        raise EvidenceError("browser artifact root must be an external directory")
    browser_artifacts: list[tuple[Path, str, str]] = []
    for index, item in enumerate(browser_artifacts_value, 1):
        if not isinstance(item, dict):
            raise EvidenceError("browser artifact entry is malformed")
        relative_value, expected_hash = item.get("path"), item.get("sha256")
        if not isinstance(relative_value, str) or not isinstance(expected_hash, str):
            raise EvidenceError("browser artifact entry is missing path or hash")
        relative = Path(relative_value)
        if relative.is_absolute() or ".." in relative.parts:
            raise EvidenceError("browser artifact path escapes its artifact root")
        source = (browser_root / relative).resolve()
        if (
            not is_within(source, browser_root)
            or not source.is_file()
            or source.stat().st_size <= 0
        ):
            raise EvidenceError(f"browser artifact is missing or empty: {source}")
        if sha256_file(source) != expected_hash:
            raise EvidenceError(f"browser artifact hash mismatch: {source}")
        browser_artifacts.append(
            (source, f"browser-artifact-{index}-{source.name}", expected_hash)
        )
    reviewer = review.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise EvidenceError("visual review requires a reviewer")
    checks_value = review.get("checks")
    if not isinstance(checks_value, list):
        raise EvidenceError("visual review checks must be a list")
    checks: list[dict[str, str]] = []
    for item in checks_value:
        if not isinstance(item, dict):
            raise EvidenceError("visual review check is malformed")
        check_id, verdict, note = item.get("id"), item.get("verdict"), item.get("note")
        if (
            not isinstance(check_id, str)
            or verdict not in VERDICTS
            or not isinstance(note, str)
            or not note.strip()
        ):
            raise EvidenceError(
                "visual review checks require id, supported verdict, and note"
            )
        checks.append({"id": check_id, "verdict": verdict, "note": note.strip()})
    checks_by_id = {item["id"]: item for item in checks}
    missing_checks = sorted(REQUIRED_CHECKS.difference(checks_by_id))
    viewports_value = review.get("viewports")
    if not isinstance(viewports_value, list) or not viewports_value:
        raise EvidenceError("visual review requires viewports")
    viewports: list[dict[str, Any]] = []
    screenshot_sources: list[tuple[Path, str]] = []
    for index, item in enumerate(viewports_value, 1):
        if not isinstance(item, dict):
            raise EvidenceError("viewport entry is malformed")
        name, width, height, screenshot_value = (
            item.get("name"),
            item.get("width"),
            item.get("height"),
            item.get("screenshot"),
        )
        if (
            not isinstance(name, str)
            or not name.strip()
            or isinstance(width, bool)
            or not isinstance(width, int)
            or width <= 0
            or isinstance(height, bool)
            or not isinstance(height, int)
            or height <= 0
            or not isinstance(screenshot_value, str)
        ):
            raise EvidenceError(
                "viewport requires name, positive width/height, and screenshot"
            )
        screenshot = Path(screenshot_value).expanduser().resolve()
        if is_within(screenshot, workspace):
            raise EvidenceError(
                "viewport screenshots must be stored outside the workspace"
            )
        if not screenshot.is_file() or screenshot.stat().st_size <= 0:
            raise EvidenceError(
                f"viewport screenshot is missing or empty: {screenshot}"
            )
        rendered_width, rendered_height, rendered_sha256 = png_identity(screenshot)
        if (rendered_width, rendered_height) != (width, height):
            raise EvidenceError(
                f"viewport dimensions do not match PNG: declared {width}x{height}, rendered {rendered_width}x{rendered_height}"
            )
        copied_name = f"viewport-{index}-{screenshot.name}"
        screenshot_sources.append((screenshot, copied_name))
        viewports.append(
            {
                "name": name.strip(),
                "width": width,
                "height": height,
                "source": str(screenshot),
                "copy": str(output / copied_name),
                "sha256": rendered_sha256,
            }
        )
    has_mobile = any(item["width"] <= 480 for item in viewports)
    has_desktop = any(item["width"] >= 1024 for item in viewports)
    required_passed = not missing_checks and all(
        checks_by_id[item]["verdict"] == "pass" for item in REQUIRED_CHECKS
    )
    output.mkdir(parents=True, exist_ok=True)
    for screenshot, copied_name in screenshot_sources:
        shutil.copyfile(screenshot, output / copied_name)
    artifacts = [
        _copy(intent_source, output, "design-intent.json"),
        _copy(browser_source, output, "playwright-receipt.json"),
        _copy(review_source, output, "visual-review.json"),
    ]
    for source, copied_name, expected_hash in browser_artifacts:
        copied = _copy(source, output, copied_name)
        if copied["sha256"] != expected_hash:
            raise EvidenceError(f"browser artifact changed while copying: {source}")
        copied["kind"] = "browser-artifact"
        artifacts.append(copied)
    for item in viewports:
        # png_identity bound the declared dimensions to this digest in one read;
        # this binds the copy to the same digest. Without it the receipt would
        # assert a dimension-matched render for an image nothing ever checked,
        # and the durable recorder trusts these declared hashes rather than
        # re-reading the PNG.
        if sha256_file(Path(item["copy"])) != item["sha256"]:
            raise EvidenceError(
                f"viewport screenshot changed while copying: {item['source']}"
            )
        artifacts.append(
            {
                "kind": "screenshot",
                "source": item["source"],
                "copy": item["copy"],
                "sha256": item["sha256"],
            }
        )
    passed = bool(has_mobile and has_desktop and required_passed)
    receipt: dict[str, Any] = {
        "type": "visual_design_evidence",
        "schemaVersion": 1,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "provider": "cognitive-powers",
        "projectRoot": str(workspace),
        "artifactRoot": str(output),
        "designIntentSha256": sha256_file(intent_source),
        "intentIdentity": intent.get("intentSha256"),
        "browserReceiptSha256": sha256_file(browser_source),
        "reviewer": reviewer.strip(),
        "checks": checks,
        "missingRequiredChecks": missing_checks,
        "viewports": viewports,
        "mobileCaptured": has_mobile,
        "desktopCaptured": has_desktop,
        "browserPassed": True,
        "visualContractPassed": passed,
        "behavioralVerificationEligible": False,
        "subjectiveQualityProven": False,
        "artifacts": artifacts,
    }
    receipt_path = output / "cognitive-design-receipt.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    receipt["receipt"] = str(receipt_path)
    return receipt, 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--workspace-root", required=True)
    create_parser.add_argument("--intent", required=True)
    create_parser.add_argument("--browser-receipt", required=True)
    create_parser.add_argument("--review", required=True)
    create_parser.add_argument("--artifact-dir", required=True)
    args = parser.parse_args()
    try:
        receipt, exit_code = create_evidence(
            args.workspace_root,
            args.intent,
            args.browser_receipt,
            args.review,
            args.artifact_dir,
        )
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return exit_code
    except (EvidenceError, OSError, ValueError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
