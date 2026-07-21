#!/usr/bin/env python3
"""Run an existing Playwright suite and emit durable normalized evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


SCHEMA_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 300
OUTPUT_TAIL_CHARS = 8_000
CONFIG_NAMES = (
    "playwright.config.ts",
    "playwright.config.js",
    "playwright.config.mts",
    "playwright.config.mjs",
    "playwright.config.cts",
    "playwright.config.cjs",
)
Runner = Callable[..., subprocess.CompletedProcess[str]]


class BrowserEvidenceError(RuntimeError):
    """Raised when browser evidence cannot be captured safely."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def resolve_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise BrowserEvidenceError(f"root is not a directory: {root}")
    return root


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_key(root: Path) -> str:
    canonical = str(root).casefold() if os.name == "nt" else str(root)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def _default_data_root() -> Path:
    configured = os.environ.get("COGNITIVE_POWERS_DATA") or os.environ.get(
        "PLUGIN_DATA"
    )
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".codex" / "cognitive-powers").resolve()


def _artifact_directory(root: Path, explicit: str | Path | None) -> Path:
    if explicit:
        directory = Path(explicit).expanduser().resolve()
    else:
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        directory = _default_data_root() / "playwright" / _project_key(root) / run_id
    if _is_within(directory, root):
        raise BrowserEvidenceError(
            f"artifact directory must be outside the workspace: {directory}"
        )
    if directory.exists() and any(directory.iterdir()):
        raise BrowserEvidenceError(f"artifact directory is not empty: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _find_config(root: Path) -> Path | None:
    for name in CONFIG_NAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _resolve_executable(root: Path, explicit: str | None) -> str | None:
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        return shutil.which(explicit)
    bin_dir = root / "node_modules" / ".bin"
    local_names = (
        ("playwright.CMD", "playwright.cmd", "playwright")
        if os.name == "nt"
        else ("playwright",)
    )
    for name in local_names:
        candidate = bin_dir / name
        if candidate.is_file():
            return str(candidate.resolve())
    return shutil.which("playwright")


def _package_manager(root: Path) -> str | None:
    for filename, manager in (
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("package-lock.json", "npm"),
        ("bun.lock", "bun"),
        ("bun.lockb", "bun"),
    ):
        if (root / filename).is_file():
            return manager
    return None


def _run(
    command: Sequence[str],
    root: Path,
    *,
    timeout_seconds: int,
    runner: Runner,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            list(command),
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BrowserEvidenceError(
            f"Playwright command failed to start: {error}"
        ) from error


def probe(
    root: str | Path,
    *,
    executable: str | None = None,
    timeout_seconds: int = 30,
    runner: Runner = subprocess.run,
) -> dict[str, object]:
    project_root = resolve_root(root)
    config = _find_config(project_root)
    resolved = _resolve_executable(project_root, executable)
    reasons: list[str] = []
    if config is None:
        reasons.append("playwright config not found at workspace root")
    if resolved is None:
        reasons.append("playwright executable not found")
    version: str | None = None
    if resolved is not None:
        completed = _run(
            [resolved, "--version"],
            project_root,
            timeout_seconds=timeout_seconds,
            runner=runner,
        )
        if completed.returncode == 0:
            version = completed.stdout.strip() or completed.stderr.strip() or None
        else:
            reasons.append("playwright version probe failed")
    return {
        "available": resolved is not None,
        "usable": not reasons,
        "reason": "; ".join(reasons) or None,
        "root": str(project_root),
        "executable": resolved,
        "config": str(config) if config else None,
        "configSha256": _sha256_file(config) if config else None,
        "version": version,
        "packageManager": _package_manager(project_root),
    }


def _iter_specs(suites: Iterable[object]) -> Iterable[dict[str, Any]]:
    for suite in suites:
        if not isinstance(suite, dict):
            continue
        for spec in suite.get("specs", []):
            if isinstance(spec, dict):
                yield spec
        yield from _iter_specs(suite.get("suites", []))


def _normalize_report(
    raw: dict[str, Any],
) -> tuple[dict[str, int], list[dict[str, object]], list[str]]:
    raw_stats = raw.get("stats") if isinstance(raw.get("stats"), dict) else {}
    stats = {
        "expected": int(raw_stats.get("expected", 0) or 0),
        "unexpected": int(raw_stats.get("unexpected", 0) or 0),
        "flaky": int(raw_stats.get("flaky", 0) or 0),
        "skipped": int(raw_stats.get("skipped", 0) or 0),
        "durationMs": int(raw_stats.get("duration", 0) or 0),
    }
    tests: list[dict[str, object]] = []
    errors: list[str] = []
    for spec in _iter_specs(raw.get("suites", [])):
        for test in spec.get("tests", []):
            if not isinstance(test, dict):
                continue
            results = (
                test.get("results", []) if isinstance(test.get("results"), list) else []
            )
            statuses = [
                str(result.get("status"))
                for result in results
                if isinstance(result, dict) and result.get("status")
            ]
            for result in results:
                if not isinstance(result, dict):
                    continue
                error = result.get("error")
                if isinstance(error, dict):
                    message = error.get("message") or error.get("value")
                    if message:
                        errors.append(str(message))
            tests.append(
                {
                    "title": spec.get("title"),
                    "file": spec.get("file"),
                    "line": spec.get("line"),
                    "project": test.get("projectName"),
                    "expectedStatus": test.get("expectedStatus"),
                    "status": test.get("status"),
                    "resultStatuses": statuses,
                    "retries": max(0, len(results) - 1),
                }
            )
    for error in raw.get("errors", []):
        if isinstance(error, dict) and (error.get("message") or error.get("value")):
            errors.append(str(error.get("message") or error.get("value")))
    return stats, tests, errors


def _artifact_manifest(
    directory: Path, excluded: set[Path] | None = None
) -> list[dict[str, object]]:
    excluded = {path.resolve() for path in (excluded or set())}
    artifacts: list[dict[str, object]] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.is_symlink() or path.resolve() in excluded:
            continue
        artifacts.append(
            {
                "path": path.relative_to(directory).as_posix(),
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    return artifacts


def run_tests(
    root: str | Path,
    *,
    executable: str | None = None,
    selectors: Sequence[str] = (),
    projects: Sequence[str] = (),
    grep: str | None = None,
    artifact_dir: str | Path | None = None,
    trace: str = "retain-on-failure",
    workers: int = 1,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    runner: Runner = subprocess.run,
) -> tuple[dict[str, object], int]:
    project_root = resolve_root(root)
    if workers < 1:
        raise BrowserEvidenceError("workers must be positive")
    if trace not in {
        "off",
        "on",
        "on-first-retry",
        "retain-on-failure",
        "retain-on-first-failure",
    }:
        raise BrowserEvidenceError(f"unsupported trace mode: {trace}")
    state = probe(
        project_root,
        executable=executable,
        timeout_seconds=min(timeout_seconds, 30),
        runner=runner,
    )
    if not state["usable"]:
        raise BrowserEvidenceError(str(state["reason"]))
    output_root = _artifact_directory(project_root, artifact_dir)
    report_path = output_root / "playwright-report.json"
    test_results = output_root / "test-results"
    command = [str(state["executable"]), "test", *selectors]
    for project in projects:
        if project.strip():
            command.append(f"--project={project.strip()}")
    if grep and grep.strip():
        command.extend(["--grep", grep.strip()])
    command.extend(
        [
            "--reporter=json",
            f"--output={test_results}",
            f"--trace={trace}",
            f"--workers={workers}",
        ]
    )
    environment = os.environ.copy()
    environment["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(report_path)
    completed = _run(
        command,
        project_root,
        timeout_seconds=timeout_seconds,
        runner=runner,
        env=environment,
    )
    raw: dict[str, Any] | None = None
    report_error: str | None = None
    if report_path.is_file():
        try:
            candidate = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(candidate, dict):
                raw = candidate
            else:
                report_error = "Playwright JSON report is not an object"
        except (OSError, json.JSONDecodeError) as error:
            report_error = f"Playwright JSON report is unreadable: {error}"
    else:
        report_error = "Playwright JSON report was not created"
    if raw is None:
        stats = {
            "expected": 0,
            "unexpected": 0,
            "flaky": 0,
            "skipped": 0,
            "durationMs": 0,
        }
        tests: list[dict[str, object]] = []
        errors = [report_error] if report_error else []
    else:
        stats, tests, errors = _normalize_report(raw)
    passed = (
        completed.returncode == 0
        and raw is not None
        and stats["expected"] > 0
        and stats["unexpected"] == 0
    )
    receipt_path = output_root / "cognitive-playwright-receipt.json"
    artifacts = _artifact_manifest(output_root, {receipt_path})
    receipt: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "type": "playwright_evidence",
        "provider": "playwright",
        "capturedAt": utc_now(),
        "root": str(project_root),
        "version": state["version"],
        "config": state["config"],
        "configSha256": state["configSha256"],
        "command": command,
        "commandStarted": True,
        "exitCode": completed.returncode,
        "passed": passed,
        "stats": stats,
        "tests": tests,
        "errors": errors,
        "stdoutSha256": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
        "stderrSha256": hashlib.sha256(completed.stderr.encode("utf-8")).hexdigest(),
        "stdoutTail": completed.stdout[-OUTPUT_TAIL_CHARS:],
        "stderrTail": completed.stderr[-OUTPUT_TAIL_CHARS:],
        "artifactRoot": str(output_root),
        "artifacts": artifacts,
    }
    receipt_path.write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    receipt["receipt"] = str(receipt_path)
    return receipt, 0 if passed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--playwright", help="Playwright executable path or command")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--json", action="store_true")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    subparsers.add_parser("probe")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--test", action="append", default=[])
    run_parser.add_argument("--project", action="append", default=[])
    run_parser.add_argument("--grep")
    run_parser.add_argument("--artifact-dir")
    run_parser.add_argument("--trace", default="retain-on-failure")
    run_parser.add_argument("--workers", type=int, default=1)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.subcommand == "probe":
            payload = probe(
                args.root, executable=args.playwright, timeout_seconds=args.timeout
            )
            exit_code = 0
        else:
            payload, exit_code = run_tests(
                args.root,
                executable=args.playwright,
                selectors=args.test,
                projects=args.project,
                grep=args.grep,
                artifact_dir=args.artifact_dir,
                trace=args.trace,
                workers=args.workers,
                timeout_seconds=args.timeout,
            )
    except BrowserEvidenceError as error:
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
