"""Pure validators for external browser, desktop, navigation, and design evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .durability import WorkStateError, _is_within, _sha256_file


def _read_payload(path: Path, kind: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        raise WorkStateError(f"{kind} receipt must be a non-empty regular file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkStateError(f"{kind} receipt is not valid JSON: {path}") from error
    if not isinstance(payload, dict):
        raise WorkStateError(f"{kind} receipt must be a JSON object")
    return payload


def _artifact_root(payload: dict[str, Any], root: Path, kind: str) -> Path:
    value = payload.get("artifactRoot")
    if not isinstance(value, str) or not value:
        raise WorkStateError(f"{kind} receipt has no artifact root")
    artifact_root = Path(value).expanduser().resolve()
    if not artifact_root.is_dir() or _is_within(artifact_root, root):
        raise WorkStateError(f"{kind} artifact root must be an external directory")
    return artifact_root


def _manifest_artifacts(
    payload: dict[str, Any],
    artifact_root: Path,
    kind: str,
    *,
    path_field: str,
    resolve_source: Callable[[str], Path],
) -> list[tuple[dict[str, Any], Path]]:
    declared = payload.get("artifacts")
    if not isinstance(declared, list) or not declared:
        raise WorkStateError(f"{kind} receipt has no artifact manifest")
    artifacts: list[tuple[dict[str, Any], Path]] = []
    for item in declared:
        if not isinstance(item, dict):
            raise WorkStateError(f"{kind} artifact entry is malformed")
        path_value, expected_hash = item.get(path_field), item.get("sha256")
        if not isinstance(path_value, str) or not isinstance(expected_hash, str):
            raise WorkStateError(f"{kind} artifact entry is missing path or hash")
        source = resolve_source(path_value)
        if source.is_symlink() or not _is_within(source, artifact_root):
            raise WorkStateError(f"{kind} artifact escapes its artifact root")
        if (
            not source.is_file()
            or source.stat().st_size <= 0
            or _sha256_file(source) != expected_hash
        ):
            raise WorkStateError(
                f"{kind} artifact is missing or hash-mismatched: {source}"
            )
        artifacts.append((item, source))
    return artifacts


def _relative_artifacts(
    payload: dict[str, Any], artifact_root: Path, kind: str
) -> list[tuple[dict[str, Any], Path]]:
    declared = payload.get("artifacts")
    if not isinstance(declared, list) or not declared:
        raise WorkStateError(f"{kind} receipt has no artifact manifest")
    artifacts: list[tuple[dict[str, Any], Path]] = []
    for item in declared:
        if not isinstance(item, dict):
            raise WorkStateError(f"{kind} artifact entry is malformed")
        path_value, expected_hash = item.get("path"), item.get("sha256")
        if not isinstance(path_value, str) or not isinstance(expected_hash, str):
            raise WorkStateError(f"{kind} artifact entry is missing path or hash")
        relative = Path(path_value)
        if relative.is_absolute() or ".." in relative.parts:
            raise WorkStateError(f"{kind} artifact path escapes its artifact root")
        source = artifact_root / relative
        if source.is_symlink():
            raise WorkStateError(f"{kind} artifact cannot be a symlink: {source}")
        resolved = source.resolve()
        if (
            not _is_within(resolved, artifact_root)
            or not resolved.is_file()
            or resolved.stat().st_size <= 0
        ):
            raise WorkStateError(f"{kind} artifact is missing or empty: {source}")
        if _sha256_file(resolved) != expected_hash:
            raise WorkStateError(f"{kind} artifact hash mismatch: {source}")
        artifacts.append((item, resolved))
    return artifacts


def _load_browser_evidence(
    path: Path, root: Path
) -> tuple[dict[str, Any], Path, list[tuple[dict[str, Any], Path]]]:
    payload = _read_payload(path, "browser")
    stats = payload.get("stats")
    if not (
        payload.get("schema_version") == 1
        and payload.get("type") == "playwright_evidence"
        and payload.get("provider") == "playwright"
        and payload.get("commandStarted") is True
        and payload.get("passed") is True
        and payload.get("exitCode") == 0
        and isinstance(stats, dict)
        and int(stats.get("expected", 0)) >= 1
        and int(stats.get("unexpected", 0)) == 0
    ):
        raise WorkStateError(
            "browser receipt does not demonstrate a passing Playwright run"
        )
    artifact_root = _artifact_root(payload, root, "browser")
    return (
        payload,
        artifact_root,
        _relative_artifacts(payload, artifact_root, "browser"),
    )


def _load_desktop_evidence(
    path: Path, root: Path
) -> tuple[dict[str, Any], Path, list[tuple[dict[str, Any], Path]]]:
    payload = _read_payload(path, "desktop")
    summary = payload.get("summary")
    if not (
        payload.get("schema_version") == 1
        and payload.get("type") == "qcu_desktop_evidence"
        and payload.get("provider") == "quick-computer-use"
        and payload.get("realActions") is True
        and payload.get("objectiveSatisfied") is True
        and payload.get("focusVerified") is True
        and payload.get("finished") is True
        and payload.get("finishReason") == "objective_verified"
        and isinstance(summary, dict)
        and int(summary.get("actionCount", 0)) >= 1
        and int(summary.get("staleFrameCount", -1)) == 0
        and int(summary.get("busyNoQueueCount", -1)) == 0
    ):
        raise WorkStateError(
            "desktop receipt does not demonstrate verified QCU completion"
        )
    artifact_root = _artifact_root(payload, root, "desktop")
    return (
        payload,
        artifact_root,
        _relative_artifacts(payload, artifact_root, "desktop"),
    )


def _load_navigation_evidence(
    path: Path, root: Path
) -> tuple[dict[str, Any], Path, list[tuple[dict[str, Any], Path]]]:
    payload = _read_payload(path, "navigation")
    if not (
        payload.get("schema_version") == 1
        and payload.get("type") == "skyvern_navigation_evidence"
        and payload.get("provider") == "skyvern"
        and payload.get("navigationOnly") is True
        and payload.get("verificationEligible") is False
        and payload.get("final") is True
        and payload.get("discoveryCompleted") is True
        and payload.get("status") == "completed"
    ):
        raise WorkStateError(
            "navigation receipt does not demonstrate completed Skyvern discovery"
        )
    artifact_root = _artifact_root(payload, root, "navigation")
    return (
        payload,
        artifact_root,
        _relative_artifacts(payload, artifact_root, "navigation"),
    )


def _load_design_evidence(
    path: Path, root: Path
) -> tuple[dict[str, Any], Path, list[tuple[dict[str, Any], Path]]]:
    payload = _read_payload(path, "design")
    if not (
        payload.get("schemaVersion") == 1
        and payload.get("type") == "visual_design_evidence"
        and payload.get("visualContractPassed") is True
        and payload.get("behavioralVerificationEligible") is False
        and payload.get("subjectiveQualityProven") is False
        and payload.get("mobileCaptured") is True
        and payload.get("desktopCaptured") is True
        and payload.get("browserPassed") is True
    ):
        raise WorkStateError(
            "design receipt does not demonstrate a completed visual contract"
        )
    artifact_root = _artifact_root(payload, root, "design")
    artifacts = _manifest_artifacts(
        payload,
        artifact_root,
        "design",
        path_field="copy",
        resolve_source=lambda value: Path(value).expanduser().resolve(),
    )
    return payload, artifact_root, artifacts
