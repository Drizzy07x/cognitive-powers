#!/usr/bin/env python3
"""Capture Skyvern discovery as navigation-only evidence and create a Playwright handoff."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence


SCHEMA_VERSION = 1
AUDITED_SKYVERN_VERSION = "1.0.47"
DEFAULT_API_BASE = "https://api.skyvern.com"
FINAL_STATUSES = frozenset(
    {"completed", "failed", "terminated", "timed_out", "canceled"}
)
SIDE_EFFECT_SCOPES = frozenset({"observe", "interact", "submit"})
HttpTransport = Callable[
    [str, str, dict[str, str], bytes | None, float], tuple[int, bytes]
]


class SkyvernEvidenceError(RuntimeError):
    """Raised when navigation evidence cannot be captured safely."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def _resolve_workspace(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise SkyvernEvidenceError(f"workspace root is not a directory: {root}")
    return root


def _project_key(root: Path) -> str:
    canonical = str(root).casefold() if os.name == "nt" else str(root)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def _data_root() -> Path:
    configured = os.environ.get("COGNITIVE_POWERS_DATA") or os.environ.get(
        "PLUGIN_DATA"
    )
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".codex" / "cognitive-powers").resolve()


def _artifact_directory(workspace: Path, explicit: str | Path | None) -> Path:
    if explicit:
        directory = Path(explicit).expanduser().resolve()
    else:
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        directory = _data_root() / "skyvern" / _project_key(workspace) / run_id
    if _is_within(directory, workspace):
        raise SkyvernEvidenceError(
            f"artifact directory must be outside the workspace: {directory}"
        )
    if directory.exists() and any(directory.iterdir()):
        raise SkyvernEvidenceError(f"artifact directory is not empty: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Receipt bytes are what their sha256 describes; newline translation would
    # make the same evidence hash differently per platform.
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _default_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SkyvernEvidenceError(
            f"Skyvern API returned HTTP {error.code}: {detail[:1000]}"
        ) from error
    except urllib.error.URLError as error:
        raise SkyvernEvidenceError(
            f"Skyvern API request failed: {error.reason}"
        ) from error


def _request_json(
    transport: HttpTransport,
    method: str,
    api_base: str,
    path: str,
    api_key: str,
    *,
    payload: object | None = None,
    timeout: float = 30,
) -> dict[str, Any] | list[Any]:
    body = (
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if payload is not None
        else None
    )
    headers = {"x-api-key": api_key, "accept": "application/json"}
    if body is not None:
        headers["content-type"] = "application/json"
    status, raw = transport(method, api_base.rstrip("/") + path, headers, body, timeout)
    if status < 200 or status >= 300:
        raise SkyvernEvidenceError(f"Skyvern API returned HTTP {status}")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SkyvernEvidenceError("Skyvern API did not return valid JSON") from error
    if not isinstance(value, (dict, list)):
        raise SkyvernEvidenceError("Skyvern API JSON must be an object or array")
    return value


def _configuration(api_base: str | None, api_key: str | None) -> tuple[str, str | None]:
    base = (api_base or os.environ.get("SKYVERN_API_BASE") or DEFAULT_API_BASE).rstrip(
        "/"
    )
    key = api_key or os.environ.get("SKYVERN_API_KEY")
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SkyvernEvidenceError(f"invalid Skyvern API base URL: {base}")
    return base, key


def probe(
    *,
    api_base: str | None = None,
    api_key: str | None = None,
    live: bool = False,
    timeout: float = 15,
    transport: HttpTransport = _default_transport,
) -> dict[str, object]:
    base, key = _configuration(api_base, api_key)
    configured = bool(key)
    live_ok: bool | None = None
    reason = None if configured else "SKYVERN_API_KEY is not configured"
    if live:
        if not key:
            live_ok = False
        else:
            _request_json(
                transport, "GET", base, "/v1/runs?limit=1", key, timeout=timeout
            )
            live_ok = True
    return {
        "available": configured,
        "usable": configured and (live_ok is not False),
        "reason": reason,
        "apiBase": base,
        "apiKeyPresent": configured,
        "liveChecked": live,
        "liveReachable": live_ok,
        "apiContract": "v1",
        "auditedOpenSourceVersion": AUDITED_SKYVERN_VERSION,
    }


def _load_schema(path: str | Path | None) -> object | None:
    if path is None:
        return None
    schema_path = Path(path).expanduser().resolve()
    try:
        value = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SkyvernEvidenceError(
            f"extraction schema is not valid JSON: {schema_path}"
        ) from error
    if not isinstance(value, (dict, list, str)):
        raise SkyvernEvidenceError(
            "extraction schema must be an object, array, or string"
        )
    return value


def _scoped_prompt(prompt: str, scope: str) -> str:
    clean = prompt.strip()
    if not clean:
        raise SkyvernEvidenceError("prompt must not be empty")
    if scope == "observe":
        return (
            "OBSERVATION ONLY: do not submit forms, confirm transactions, create, update, or delete "
            "remote data, send messages, upload or download files. " + clean
        )
    if scope == "interact":
        return (
            "INTERACT WITHOUT FINAL SUBMISSION: do not commit remote changes or submit forms. "
            + clean
        )
    return clean


def _require_run_shape(run: object) -> dict[str, Any]:
    if not isinstance(run, dict):
        raise SkyvernEvidenceError("Skyvern run response must be an object")
    run_id = run.get("run_id")
    status = run.get("status")
    if (
        not isinstance(run_id, str)
        or not run_id
        or not isinstance(status, str)
        or not status
    ):
        raise SkyvernEvidenceError("Skyvern run response is missing run_id or status")
    return run


def _artifact_manifest(
    directory: Path, excluded: set[Path] | None = None
) -> list[dict[str, object]]:
    excluded_resolved = {path.resolve() for path in (excluded or set())}
    artifacts: list[dict[str, object]] = []
    for path in sorted(directory.rglob("*")):
        if (
            not path.is_file()
            or path.is_symlink()
            or path.resolve() in excluded_resolved
        ):
            continue
        artifacts.append(
            {
                "path": path.relative_to(directory).as_posix(),
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    return artifacts


def _normalize(
    run: dict[str, Any],
    *,
    workspace: Path,
    request_payload: dict[str, Any] | None,
    side_effect_scope: str,
    artifact_root: Path,
    source: str,
    timeline_available: bool,
    artifact_index_available: bool,
) -> dict[str, object]:
    status = str(run["status"])
    receipt_path = artifact_root / "cognitive-skyvern-receipt.json"
    artifacts = _artifact_manifest(artifact_root, {receipt_path})
    output = run.get("output")
    return {
        "schema_version": SCHEMA_VERSION,
        "type": "skyvern_navigation_evidence",
        "provider": "skyvern",
        "navigationOnly": True,
        "verificationEligible": False,
        "capturedAt": utc_now(),
        "source": source,
        "workspaceRoot": str(workspace),
        "apiContract": "v1",
        "auditedOpenSourceVersion": AUDITED_SKYVERN_VERSION,
        "runId": run["run_id"],
        "status": status,
        "final": status in FINAL_STATUSES,
        "discoveryCompleted": status == "completed",
        "sideEffectScope": side_effect_scope,
        "request": request_payload,
        "output": output,
        "outputSha256": _sha256_bytes(
            json.dumps(output, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ),
        "stepCount": run.get("step_count"),
        "runType": run.get("run_type"),
        "failureReason": run.get("failure_reason"),
        "errors": run.get("errors") or [],
        "recordingUrl": run.get("recording_url"),
        "screenshotUrls": run.get("screenshot_urls") or [],
        "remoteArtifactsDownloaded": False,
        "timelineMetadataCaptured": timeline_available,
        "artifactIndexCaptured": artifact_index_available,
        "artifactRoot": str(artifact_root),
        "artifacts": artifacts,
        "rawResponseSha256": _sha256_file(artifact_root / "run-final.json"),
    }


def run_task(
    workspace_root: str | Path,
    *,
    prompt: str,
    url: str,
    execute: bool,
    side_effect_scope: str = "observe",
    allow_side_effects: bool = False,
    max_steps: int = 10,
    extraction_schema: str | Path | None = None,
    api_base: str | None = None,
    api_key: str | None = None,
    artifact_dir: str | Path | None = None,
    timeout_seconds: float = 180,
    poll_seconds: float = 2,
    transport: HttpTransport = _default_transport,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, object], int]:
    workspace = _resolve_workspace(workspace_root)
    if not execute:
        raise SkyvernEvidenceError("live Skyvern execution requires --execute")
    if side_effect_scope not in SIDE_EFFECT_SCOPES:
        raise SkyvernEvidenceError(
            f"unsupported side-effect scope: {side_effect_scope}"
        )
    if side_effect_scope == "submit" and not allow_side_effects:
        raise SkyvernEvidenceError("submit scope requires --allow-side-effects")
    if not 1 <= max_steps <= 50:
        raise SkyvernEvidenceError("max-steps must be between 1 and 50")
    parsed_url = urllib.parse.urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise SkyvernEvidenceError(f"invalid starting URL: {url}")
    base, key = _configuration(api_base, api_key)
    if not key:
        raise SkyvernEvidenceError("SKYVERN_API_KEY is not configured")
    output_root = _artifact_directory(workspace, artifact_dir)
    request_payload: dict[str, Any] = {
        "prompt": _scoped_prompt(prompt, side_effect_scope),
        "url": url,
        "max_steps": max_steps,
        "include_action_history_in_verification": True,
    }
    schema = _load_schema(extraction_schema)
    if schema is not None:
        request_payload["data_extraction_schema"] = schema
    _write_json(output_root / "request.json", request_payload)
    created = _request_json(
        transport,
        "POST",
        base,
        "/v1/run/tasks",
        key,
        payload=request_payload,
        timeout=min(timeout_seconds, 30),
    )
    run = _require_run_shape(created)
    _write_json(output_root / "run-created.json", run)
    deadline = time.monotonic() + timeout_seconds
    while str(run["status"]) not in FINAL_STATUSES:
        if time.monotonic() >= deadline:
            raise SkyvernEvidenceError(
                f"timed out waiting for Skyvern run {run['run_id']}"
            )
        sleep(max(0.1, min(poll_seconds, 10)))
        current = _request_json(
            transport,
            "GET",
            base,
            f"/v1/runs/{urllib.parse.quote(str(run['run_id']), safe='')}",
            key,
            timeout=min(timeout_seconds, 30),
        )
        run = _require_run_shape(current)
    _write_json(output_root / "run-final.json", run)
    timeline_available = False
    artifact_index_available = False
    for name, path in (
        (
            "timeline",
            f"/v1/runs/{urllib.parse.quote(str(run['run_id']), safe='')}/timeline",
        ),
        (
            "artifact-index",
            f"/v1/runs/{urllib.parse.quote(str(run['run_id']), safe='')}/artifacts",
        ),
    ):
        try:
            value = _request_json(
                transport, "GET", base, path, key, timeout=min(timeout_seconds, 30)
            )
            _write_json(output_root / f"{name}.json", value)
            if name == "timeline":
                timeline_available = True
            else:
                artifact_index_available = True
        except SkyvernEvidenceError as error:
            _write_json(output_root / f"{name}-error.json", {"error": str(error)})
    receipt = _normalize(
        run,
        workspace=workspace,
        request_payload=request_payload,
        side_effect_scope=side_effect_scope,
        artifact_root=output_root,
        source="live-api",
        timeline_available=timeline_available,
        artifact_index_available=artifact_index_available,
    )
    receipt_path = output_root / "cognitive-skyvern-receipt.json"
    _write_json(receipt_path, receipt)
    receipt["receipt"] = str(receipt_path)
    return receipt, 0 if receipt["discoveryCompleted"] else 1


def ingest(
    workspace_root: str | Path,
    response_path: str | Path,
    *,
    request_path: str | Path | None = None,
    timeline_path: str | Path | None = None,
    artifact_index_path: str | Path | None = None,
    side_effect_scope: str = "observe",
    artifact_dir: str | Path | None = None,
) -> tuple[dict[str, object], int]:
    workspace = _resolve_workspace(workspace_root)
    if side_effect_scope not in SIDE_EFFECT_SCOPES:
        raise SkyvernEvidenceError(
            f"unsupported side-effect scope: {side_effect_scope}"
        )
    output_root = _artifact_directory(workspace, artifact_dir)

    def load_object(value: str | Path, label: str) -> object:
        path = Path(value).expanduser().resolve()
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SkyvernEvidenceError(f"{label} is not valid JSON: {path}") from error

    run = _require_run_shape(load_object(response_path, "run response"))
    request_payload = load_object(request_path, "request") if request_path else None
    if request_payload is not None and not isinstance(request_payload, dict):
        raise SkyvernEvidenceError("request JSON must be an object")
    _write_json(output_root / "run-final.json", run)
    if request_payload is not None:
        _write_json(output_root / "request.json", request_payload)
    if timeline_path:
        _write_json(
            output_root / "timeline.json", load_object(timeline_path, "timeline")
        )
    if artifact_index_path:
        _write_json(
            output_root / "artifact-index.json",
            load_object(artifact_index_path, "artifact index"),
        )
    receipt = _normalize(
        run,
        workspace=workspace,
        request_payload=request_payload,
        side_effect_scope=side_effect_scope,
        artifact_root=output_root,
        source="ingested-response",
        timeline_available=bool(timeline_path),
        artifact_index_available=bool(artifact_index_path),
    )
    receipt_path = output_root / "cognitive-skyvern-receipt.json"
    _write_json(receipt_path, receipt)
    receipt["receipt"] = str(receipt_path)
    return receipt, 0 if receipt["discoveryCompleted"] else 1


def handoff(
    workspace_root: str | Path,
    receipt_value: str | Path,
    *,
    output_dir: str | Path | None = None,
) -> dict[str, object]:
    workspace = _resolve_workspace(workspace_root)
    receipt_path = Path(receipt_value).expanduser().resolve()
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SkyvernEvidenceError(
            f"Skyvern receipt is not valid JSON: {receipt_path}"
        ) from error
    if (
        not isinstance(receipt, dict)
        or receipt.get("type") != "skyvern_navigation_evidence"
        or receipt.get("navigationOnly") is not True
        or receipt.get("verificationEligible") is not False
    ):
        raise SkyvernEvidenceError("unsupported or unsafe Skyvern receipt")
    destination = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else receipt_path.parent / "playwright-handoff"
    )
    if _is_within(destination, workspace):
        raise SkyvernEvidenceError("handoff directory must be outside the workspace")
    if destination.exists() and any(destination.iterdir()):
        raise SkyvernEvidenceError(f"handoff directory is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    request = receipt.get("request") if isinstance(receipt.get("request"), dict) else {}
    start_url = (
        request.get("url") if isinstance(request.get("url"), str) else "about:blank"
    )
    goal = (
        request.get("prompt")
        if isinstance(request.get("prompt"), str)
        else "Discovered browser flow"
    )
    receipt_hash = _sha256_file(receipt_path)
    plan = {
        "schema_version": 1,
        "type": "playwright_test_candidate",
        "verificationEligible": False,
        "failClosed": True,
        "sourceRunId": receipt.get("runId"),
        "sourceReceipt": str(receipt_path),
        "sourceReceiptSha256": receipt_hash,
        "startUrl": start_url,
        "goal": goal,
        "observedStepCount": receipt.get("stepCount"),
        "requiredNextStep": "Replace the failing placeholder with deterministic actions and a user-visible assertion, then establish red/green evidence.",
    }
    plan_path = destination / "handoff.json"
    spec_path = destination / "discovered-flow.spec.js"
    _write_json(plan_path, plan)
    spec = (
        "const { test, expect } = require('@playwright/test');\n\n"
        "test('discovered flow requires deterministic verification', async ({ page }) => {\n"
        f"  await page.goto({json.dumps(start_url)});\n"
        f"  // Skyvern goal: {goal.replace(chr(10), ' ')[:500]}\n"
        "  // Replace this fail-closed placeholder with explicit actions and a user-visible assertion.\n"
        "  expect(false, 'Skyvern discovery is not Playwright verification').toBe(true);\n"
        "});\n"
    )
    spec_path.write_text(spec, encoding="utf-8", newline="\n")
    return {
        **plan,
        "plan": str(plan_path),
        "candidate": str(spec_path),
        "candidateSha256": _sha256_file(spec_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--api-base")
    parser.add_argument("--api-key")
    parser.add_argument("--json", action="store_true")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    probe_parser = subparsers.add_parser("probe")
    probe_parser.add_argument("--live", action="store_true")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--prompt", required=True)
    run_parser.add_argument("--url", required=True)
    run_parser.add_argument("--execute", action="store_true")
    run_parser.add_argument(
        "--side-effect-scope", choices=sorted(SIDE_EFFECT_SCOPES), default="observe"
    )
    run_parser.add_argument("--allow-side-effects", action="store_true")
    run_parser.add_argument("--max-steps", type=int, default=10)
    run_parser.add_argument("--extraction-schema")
    run_parser.add_argument("--artifact-dir")
    run_parser.add_argument("--timeout", type=float, default=180)
    ingest_parser = subparsers.add_parser("ingest")
    ingest_parser.add_argument("--response", required=True)
    ingest_parser.add_argument("--request")
    ingest_parser.add_argument("--timeline")
    ingest_parser.add_argument("--artifact-index")
    ingest_parser.add_argument(
        "--side-effect-scope", choices=sorted(SIDE_EFFECT_SCOPES), default="observe"
    )
    ingest_parser.add_argument("--artifact-dir")
    handoff_parser = subparsers.add_parser("handoff")
    handoff_parser.add_argument("--receipt", required=True)
    handoff_parser.add_argument("--output-dir")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.subcommand == "probe":
            payload = probe(
                api_base=args.api_base, api_key=args.api_key, live=args.live
            )
            exit_code = 0
        elif args.subcommand == "run":
            payload, exit_code = run_task(
                args.root,
                prompt=args.prompt,
                url=args.url,
                execute=args.execute,
                side_effect_scope=args.side_effect_scope,
                allow_side_effects=args.allow_side_effects,
                max_steps=args.max_steps,
                extraction_schema=args.extraction_schema,
                api_base=args.api_base,
                api_key=args.api_key,
                artifact_dir=args.artifact_dir,
                timeout_seconds=args.timeout,
            )
        elif args.subcommand == "ingest":
            payload, exit_code = ingest(
                args.root,
                args.response,
                request_path=args.request,
                timeline_path=args.timeline,
                artifact_index_path=args.artifact_index,
                side_effect_scope=args.side_effect_scope,
                artifact_dir=args.artifact_dir,
            )
        else:
            payload = handoff(args.root, args.receipt, output_dir=args.output_dir)
            exit_code = 0
    except SkyvernEvidenceError as error:
        payload = {"error": str(error)}
        exit_code = 2
    print(
        json.dumps(payload, indent=2, ensure_ascii=False)
        if args.json
        else json.dumps(payload, ensure_ascii=False)
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
