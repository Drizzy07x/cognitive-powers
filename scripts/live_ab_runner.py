#!/usr/bin/env python3
"""Run isolated, paired Codex live evaluations without inventing evidence."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts.integration_evaluation import validate_task_contract
except ModuleNotFoundError:  # Direct script execution places scripts/ on sys.path.
    from integration_evaluation import validate_task_contract


IGNORED_PARTS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache"}


class LiveEvaluationError(ValueError):
    """Raised when a live evaluation cannot produce trustworthy receipts."""


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_layout(
    fixture: Path,
    output: Path,
    baseline_home: Path,
    candidate_home: Path,
) -> tuple[Path, Path, Path, Path]:
    fixture = _resolved(fixture)
    output = _resolved(output)
    baseline_home = _resolved(baseline_home)
    candidate_home = _resolved(candidate_home)
    if not fixture.is_dir():
        raise LiveEvaluationError(f"fixture is not a directory: {fixture}")
    for label, home in (("baseline", baseline_home), ("candidate", candidate_home)):
        if not home.is_dir():
            raise LiveEvaluationError(f"{label} CODEX_HOME is not a directory: {home}")
    if baseline_home == candidate_home:
        raise LiveEvaluationError("baseline and candidate CODEX_HOME must differ")
    if _is_within(output, fixture) or _is_within(fixture, output):
        raise LiveEvaluationError(
            "output and source fixture must not contain each other"
        )
    for home in (baseline_home, candidate_home):
        if _is_within(output, home) or _is_within(home, output):
            raise LiveEvaluationError(
                "output and CODEX_HOME must not contain each other"
            )
    return fixture, output, baseline_home, candidate_home


def tree_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in IGNORED_PARTS for part in path.parts):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        relative = path.relative_to(root).as_posix()
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def source_sha256(hashes: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for path, value in sorted(hashes.items()):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def protected_roots(
    explicit: Sequence[Path], fixture: Path, plugin_identity: dict[str, Any]
) -> list[Path]:
    candidates = [
        *explicit,
        fixture,
        Path(plugin_identity["source_root"]),
        Path(plugin_identity["installed_root"]),
    ]
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = _resolved(candidate)
        if not resolved.is_dir():
            raise LiveEvaluationError(f"guarded root is not a directory: {resolved}")
        key = os.path.normcase(str(resolved))
        if key not in seen:
            result.append(resolved)
            seen.add(key)
    return result


def snapshot_guards(roots: Sequence[Path]) -> dict[str, dict[str, str]]:
    return {str(root): tree_hashes(root) for root in roots}


def verify_guards(
    before: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    changes: dict[str, list[str]] = {}
    for path, initial in before.items():
        final = tree_hashes(Path(path))
        changed = changed_paths(initial, final)
        receipts.append(
            {
                "path": path,
                "before_sha256": source_sha256(initial),
                "after_sha256": source_sha256(final),
                "before_file_count": len(initial),
                "after_file_count": len(final),
                "stable": not changed,
            }
        )
        if changed:
            changes[path] = changed
    if changes:
        raise LiveEvaluationError(f"guarded roots changed: {changes}")
    return receipts


def command_identity(argv: Sequence[str]) -> dict[str, Any]:
    files: dict[str, str] = {}
    for value in argv:
        path = Path(value).expanduser()
        if path.is_file():
            resolved = path.resolve()
            files[str(resolved)] = hashlib.sha256(resolved.read_bytes()).hexdigest()
    payload = json.dumps(
        {"argv": list(argv), "files": files}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "files": files,
    }


def changed_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(
        path for path in set(before) | set(after) if before.get(path) != after.get(path)
    )


def unexpected_changes(paths: Sequence[str], allowed: Sequence[str]) -> list[str]:
    return [
        path
        for path in paths
        if not any(fnmatch.fnmatchcase(path, rule) for rule in allowed)
    ]


def arm_order(repetitions: int, seed: str) -> list[list[str]]:
    if repetitions < 2:
        raise LiveEvaluationError("repetitions must be at least 2")
    if not seed.strip():
        raise LiveEvaluationError("seed must not be empty")
    first = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16) % 2
    orders: list[list[str]] = []
    for repetition in range(repetitions):
        baseline_first = (repetition + first) % 2 == 0
        orders.append(
            ["baseline", "candidate"] if baseline_first else ["candidate", "baseline"]
        )
    return orders


def parse_events(path: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    invalid_lines = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        if isinstance(value, dict):
            rows.append(value)
    completed = [row for row in rows if row.get("type") == "turn.completed"]
    if invalid_lines:
        raise LiveEvaluationError(f"{path} contains {invalid_lines} non-JSON lines")
    if len(completed) != 1 or not isinstance(completed[0].get("usage"), dict):
        raise LiveEvaluationError(
            f"{path} must contain exactly one completed turn with usage"
        )
    usage = completed[0]["usage"]
    required = ("input_tokens", "cached_input_tokens", "output_tokens")
    if any(
        not isinstance(usage.get(field), int) or usage[field] < 0 for field in required
    ):
        raise LiveEvaluationError(f"{path} contains invalid provider usage")
    if usage["cached_input_tokens"] > usage["input_tokens"]:
        raise LiveEvaluationError(f"{path} cached input exceeds input tokens")
    item_types = [
        row.get("item", {}).get("type")
        for row in rows
        if row.get("type") == "item.completed" and isinstance(row.get("item"), dict)
    ]
    tool_calls = sum(
        item_type not in {None, "agent_message", "reasoning"}
        for item_type in item_types
    )
    return {
        "usage": dict(usage),
        "tool_calls": tool_calls,
        "invalid_json_lines": invalid_lines,
    }


def _plugin_list(codex: str, home: Path) -> list[dict[str, Any]]:
    environment = dict(os.environ)
    environment["CODEX_HOME"] = str(home)
    completed = subprocess.run(
        [codex, "plugin", "list", "--json"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if completed.returncode != 0:
        raise LiveEvaluationError(
            f"plugin preflight failed for {home}: {(completed.stderr or completed.stdout)[-500:]}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise LiveEvaluationError(
            f"plugin preflight returned invalid JSON for {home}"
        ) from error
    installed = payload.get("installed")
    if not isinstance(installed, list) or not all(
        isinstance(item, dict) for item in installed
    ):
        raise LiveEvaluationError(
            f"plugin preflight returned invalid installed list for {home}"
        )
    return installed


def _candidate_identity(item: dict[str, Any], candidate_home: Path) -> dict[str, Any]:
    version = item.get("version")
    name = item.get("name")
    marketplace = item.get("marketplaceName")
    source = item.get("source")
    source_path = source.get("path") if isinstance(source, dict) else None
    if not all(
        isinstance(value, str) and value
        for value in (version, name, marketplace, source_path)
    ):
        raise LiveEvaluationError("candidate plugin identity is incomplete")
    source_root = _resolved(Path(source_path))
    installed_root = (
        candidate_home / "plugins" / "cache" / marketplace / name / version
    ).resolve()
    if not source_root.is_dir() or not installed_root.is_dir():
        raise LiveEvaluationError(
            "candidate source or installed plugin directory is missing"
        )
    source_hashes = tree_hashes(source_root)
    installed_hashes = tree_hashes(installed_root)
    if source_hashes != installed_hashes:
        changed = changed_paths(source_hashes, installed_hashes)
        raise LiveEvaluationError(
            "candidate installation differs from source: " + ", ".join(changed[:20])
        )
    identity = source_sha256(source_hashes)
    return {
        "version": version,
        "source_root": str(source_root),
        "installed_root": str(installed_root),
        "source_sha256": identity,
        "installed_sha256": identity,
        "file_count": len(source_hashes),
    }


def validate_arm_plugins(
    codex: str, baseline_home: Path, candidate_home: Path
) -> dict[str, Any]:
    baseline = _plugin_list(codex, baseline_home)
    candidate = _plugin_list(codex, candidate_home)
    if any(item.get("pluginId") == "cognitive-powers@personal" for item in baseline):
        raise LiveEvaluationError("baseline CODEX_HOME contains Cognitive Powers")
    cognitive = [
        item
        for item in candidate
        if item.get("pluginId") == "cognitive-powers@personal"
        and item.get("installed") is True
        and item.get("enabled") is True
    ]
    if len(cognitive) != 1:
        raise LiveEvaluationError(
            "candidate CODEX_HOME must contain one enabled Cognitive Powers"
        )
    baseline_other = sorted(item.get("pluginId") for item in baseline)
    candidate_other = sorted(
        item.get("pluginId")
        for item in candidate
        if item.get("pluginId") != "cognitive-powers@personal"
    )
    if baseline_other != candidate_other:
        raise LiveEvaluationError("arms contain different non-candidate plugin sets")
    return _candidate_identity(cognitive[0], candidate_home)


def load_task_binding(
    path: Path,
    *,
    task_id: str,
    prompt: str,
    repetitions: int,
    seed: str,
) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LiveEvaluationError(f"cannot read task contract: {error}") from error
    try:
        contract = validate_task_contract(raw)
    except ValueError as error:
        raise LiveEvaluationError(f"invalid task contract: {error}") from error
    task = contract["tasks"].get(task_id)
    if task is None:
        raise LiveEvaluationError(f"task contract does not contain {task_id}")
    round_value = contract["rounds"][task["split"]]
    expected = {
        "prompt": task["prompt"],
        "repetitions": round_value["repetitions_per_task"],
        "seed": round_value["arm_order"]["seed"],
    }
    actual = {"prompt": prompt, "repetitions": repetitions, "seed": seed}
    mismatches = [field for field in expected if expected[field] != actual[field]]
    if mismatches:
        raise LiveEvaluationError(
            "task run does not match frozen contract: " + ", ".join(mismatches)
        )
    return {
        "task_set_id": contract["task_set_id"],
        "task_version": task["version"],
        "split": task["split"],
        "fixture_id": task["fixture_id"],
        "randomization_seed": expected["seed"],
    }


def _replace_fixture(argv: Sequence[str], fixture: Path) -> list[str]:
    return [value.replace("{fixture}", str(fixture)) for value in argv]


def normalize_quality_payload(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LiveEvaluationError("quality check must return one JSON object")
    score = value.get("score")
    evidence = value.get("evidence")
    critical = value.get("critical_errors")
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not 0 <= score <= 100
    ):
        raise LiveEvaluationError("quality score must be a number from 0 to 100")
    if (
        not isinstance(evidence, list)
        or not evidence
        or not all(isinstance(item, str) and item for item in evidence)
    ):
        raise LiveEvaluationError("quality evidence must be a non-empty string list")
    if not isinstance(critical, list) or not all(
        isinstance(item, str) and item for item in critical
    ):
        raise LiveEvaluationError("quality critical_errors must be a string list")
    return {
        "score": float(score),
        "evidence": list(evidence),
        "critical_errors": list(critical),
    }


def aggregate_results(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_repetition: dict[int, dict[str, dict[str, Any]]] = {}
    for result in results:
        repetition = result.get("repetition")
        variant = result.get("variant")
        if not isinstance(repetition, int) or variant not in {"baseline", "candidate"}:
            raise LiveEvaluationError(
                "results contain an invalid repetition or variant"
            )
        variants = by_repetition.setdefault(repetition, {})
        if variant in variants:
            raise LiveEvaluationError("results contain a duplicate arm")
        variants[variant] = result
    if not by_repetition or any(
        set(variants) != {"baseline", "candidate"}
        for variants in by_repetition.values()
    ):
        raise LiveEvaluationError("results must contain complete paired arms")

    metrics: dict[str, Any] = {}
    for field in (
        "total_tokens",
        "fresh_input_tokens",
        "output_tokens",
        "tool_calls",
        "elapsed_seconds",
    ):
        baseline = statistics.median(
            variants["baseline"][field] for variants in by_repetition.values()
        )
        candidate = statistics.median(
            variants["candidate"][field] for variants in by_repetition.values()
        )
        metrics[field] = {
            "baseline_median": baseline,
            "candidate_median": candidate,
            "delta_percent": round(((candidate - baseline) / baseline) * 100, 3)
            if baseline
            else None,
        }
    pair_total_deltas = [
        round(
            (
                variants["candidate"]["total_tokens"]
                - variants["baseline"]["total_tokens"]
            )
            / variants["baseline"]["total_tokens"]
            * 100,
            3,
        )
        for variants in by_repetition.values()
    ]
    return {
        "pair_count": len(by_repetition),
        "all_pairs_successful": all(
            variants[arm]["success"]
            for variants in by_repetition.values()
            for arm in ("baseline", "candidate")
        ),
        "metrics": metrics,
        "pair_total_token_delta_percent": pair_total_deltas,
        "worst_pair_total_token_delta_percent": max(pair_total_deltas),
    }


def _run_one(
    *,
    codex: str,
    home: Path,
    fixture: Path,
    artifact_prefix: Path,
    prompt: str,
    model: str,
    reasoning_effort: str,
    hidden_check: Sequence[str],
    quality_check: Sequence[str] | None,
    allowed_changes: Sequence[str],
    bypass_sandbox: bool,
    session_timeout_seconds: int,
) -> dict[str, Any]:
    events = Path(f"{artifact_prefix}-events.jsonl")
    stderr = Path(f"{artifact_prefix}-stderr.log")
    message = Path(f"{artifact_prefix}-message.txt")
    environment = dict(os.environ)
    environment["CODEX_HOME"] = str(home)
    command = [
        codex,
        "exec",
        "--ephemeral",
        "--json",
        "--color",
        "never",
        "--skip-git-repo-check",
        "--cd",
        str(fixture),
        "--output-last-message",
        str(message),
        "--model",
        model,
        "-c",
        f'model_reasoning_effort="{reasoning_effort}"',
    ]
    command.extend(
        ["--dangerously-bypass-approvals-and-sandbox"]
        if bypass_sandbox
        else ["--sandbox", "workspace-write"]
    )
    command.append(prompt)
    started = time.monotonic()
    with (
        events.open("w", encoding="utf-8", newline="\n") as stream,
        stderr.open("w", encoding="utf-8", newline="\n") as error_stream,
    ):
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=stream,
                stderr=error_stream,
                text=True,
                env=environment,
                timeout=session_timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise LiveEvaluationError(
                f"Codex session exceeded {session_timeout_seconds} seconds"
            ) from error
    elapsed = time.monotonic() - started
    parsed = parse_events(events)
    if not message.is_file() or not message.read_text(encoding="utf-8").strip():
        raise LiveEvaluationError(f"missing final message for {artifact_prefix.name}")
    try:
        hidden = subprocess.run(
            _replace_fixture(hidden_check, fixture),
            check=False,
            capture_output=True,
            text=True,
            cwd=fixture,
            timeout=session_timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise LiveEvaluationError(
            f"hidden check exceeded {session_timeout_seconds} seconds"
        ) from error
    before_path = Path(f"{artifact_prefix}-initial-hashes.json")
    initial = json.loads(before_path.read_text(encoding="utf-8"))
    changed = changed_paths(initial, tree_hashes(fixture))
    out_of_scope = unexpected_changes(changed, allowed_changes)
    quality = {
        "score": 100.0 if hidden.returncode == 0 and not out_of_scope else 0.0,
        "evidence": ["default binary hidden-check and scope rubric"],
        "critical_errors": [],
    }
    if quality_check is not None:
        replacements = {
            "{fixture}": str(fixture),
            "{events}": str(events),
            "{message}": str(message),
            "{stderr}": str(stderr),
        }
        quality_command = []
        for value in quality_check:
            next_value = value
            for old, new in replacements.items():
                next_value = next_value.replace(old, new)
            quality_command.append(next_value)
        try:
            quality_completed = subprocess.run(
                quality_command,
                check=False,
                capture_output=True,
                text=True,
                cwd=fixture,
                timeout=session_timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise LiveEvaluationError(
                f"quality check exceeded {session_timeout_seconds} seconds"
            ) from error
        if quality_completed.returncode != 0:
            raise LiveEvaluationError(
                "quality check failed: "
                + (quality_completed.stderr or quality_completed.stdout)[-1000:]
            )
        try:
            quality_raw = json.loads(quality_completed.stdout)
        except json.JSONDecodeError as error:
            raise LiveEvaluationError("quality check returned invalid JSON") from error
        quality = normalize_quality_payload(quality_raw)
    critical: list[str] = []
    if completed.returncode != 0:
        critical.append(f"codex exit {completed.returncode}")
    if hidden.returncode != 0:
        critical.append(f"hidden check exit {hidden.returncode}")
    if out_of_scope:
        critical.append("out-of-scope changes: " + ", ".join(out_of_scope))
    critical.extend(quality["critical_errors"])
    usage = parsed["usage"]
    return {
        "success": not critical,
        "critical_errors": critical,
        "codex_exit": completed.returncode,
        "hidden_exit": hidden.returncode,
        "hidden_stdout_tail": hidden.stdout[-1000:],
        "hidden_stderr_tail": hidden.stderr[-1000:],
        "changed_paths": changed,
        "out_of_scope_changes": out_of_scope,
        "quality_score": quality["score"] / 100.0,
        "quality_evidence": quality["evidence"],
        "input_tokens": usage["input_tokens"],
        "cached_input_tokens": usage["cached_input_tokens"],
        "fresh_input_tokens": usage["input_tokens"] - usage["cached_input_tokens"],
        "output_tokens": usage["output_tokens"],
        "total_tokens": usage["input_tokens"] + usage["output_tokens"],
        "tool_calls": parsed["tool_calls"],
        "elapsed_seconds": round(elapsed, 3),
        "events": str(events),
        "stderr": str(stderr),
        "message": str(message),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-home", type=Path, required=True)
    parser.add_argument("--candidate-home", type=Path, required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--task-contract", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--hidden-check-json", required=True)
    parser.add_argument("--quality-check-json")
    parser.add_argument("--allow-change", action="append", default=[])
    parser.add_argument("--guard-root", type=Path, action="append", default=[])
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--bypass-sandbox", action="store_true")
    parser.add_argument("--session-timeout-seconds", type=int, default=1800)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        fixture, output, baseline_home, candidate_home = validate_layout(
            args.fixture, args.output, args.baseline_home, args.candidate_home
        )
        try:
            hidden_check = json.loads(args.hidden_check_json)
        except json.JSONDecodeError as error:
            raise LiveEvaluationError(
                "hidden check must be a JSON argv array"
            ) from error
        if (
            not isinstance(hidden_check, list)
            or not hidden_check
            or not all(isinstance(value, str) and value for value in hidden_check)
        ):
            raise LiveEvaluationError(
                "hidden check must be a non-empty JSON argv array"
            )
        if not args.allow_change:
            raise LiveEvaluationError("at least one --allow-change pattern is required")
        if args.session_timeout_seconds < 1:
            raise LiveEvaluationError("session timeout must be positive")
        binding = (
            load_task_binding(
                args.task_contract,
                task_id=args.task_id,
                prompt=args.prompt,
                repetitions=args.repetitions,
                seed=args.seed,
            )
            if args.task_contract is not None
            else None
        )
        quality_check = None
        if args.quality_check_json is not None:
            try:
                quality_check = json.loads(args.quality_check_json)
            except json.JSONDecodeError as error:
                raise LiveEvaluationError(
                    "quality check must be a JSON argv array"
                ) from error
            if (
                not isinstance(quality_check, list)
                or not quality_check
                or not all(isinstance(value, str) and value for value in quality_check)
            ):
                raise LiveEvaluationError(
                    "quality check must be a non-empty JSON argv array"
                )
        if binding is not None and quality_check is None:
            raise LiveEvaluationError(
                "contract-bound runs require an external quality check"
            )
        hidden_identity = command_identity(hidden_check)
        quality_identity = (
            command_identity(quality_check) if quality_check is not None else None
        )
        if output.exists():
            raise LiveEvaluationError(f"output already exists: {output}")
        plugin_identity = validate_arm_plugins(
            args.codex, baseline_home, candidate_home
        )
        guards = protected_roots(args.guard_root, fixture, plugin_identity)
        guard_before = snapshot_guards(guards)
        plugin_version = plugin_identity["version"]
        fixture_hashes = tree_hashes(fixture)
        fixture_sha = source_sha256(fixture_hashes)
        output.mkdir(parents=True)
        artifacts = output / "artifacts"
        runs_root = output / "runs"
        artifacts.mkdir()
        runs_root.mkdir()
        results: list[dict[str, Any]] = []
        orders = arm_order(args.repetitions, args.seed)
        homes = {"baseline": baseline_home, "candidate": candidate_home}
        for repetition, order in enumerate(orders, start=1):
            for arm in order:
                run_root = runs_root / f"rep{repetition}-{arm}"
                shutil.copytree(fixture, run_root)
                copied_hashes = tree_hashes(run_root)
                if copied_hashes != fixture_hashes:
                    raise LiveEvaluationError(
                        f"fresh fixture copy mismatch for rep{repetition}-{arm}"
                    )
                prefix = artifacts / f"rep{repetition}-{arm}"
                Path(f"{prefix}-initial-hashes.json").write_text(
                    json.dumps(copied_hashes, sort_keys=True), encoding="utf-8"
                )
                result = _run_one(
                    codex=args.codex,
                    home=homes[arm],
                    fixture=run_root,
                    artifact_prefix=prefix,
                    prompt=args.prompt,
                    model=args.model,
                    reasoning_effort=args.reasoning_effort,
                    hidden_check=hidden_check,
                    quality_check=quality_check,
                    allowed_changes=args.allow_change,
                    bypass_sandbox=args.bypass_sandbox,
                    session_timeout_seconds=args.session_timeout_seconds,
                )
                result.update(
                    {
                        "case_id": f"{args.task_id}-rep{repetition}",
                        "task_id": args.task_id,
                        "repetition": repetition,
                        "variant": arm,
                        "arm_order": order,
                        "source_sha256": fixture_sha,
                        "plugin_version": plugin_version
                        if arm == "candidate"
                        else None,
                    }
                )
                results.append(result)
        postflight_plugin_identity = validate_arm_plugins(
            args.codex, baseline_home, candidate_home
        )
        if postflight_plugin_identity != plugin_identity:
            raise LiveEvaluationError(
                "candidate plugin identity changed between preflight and postflight"
            )
        guard_receipts = verify_guards(guard_before)
        receipts = []
        for result in results:
            receipt = {
                "schema_version": 2 if binding is not None else 1,
                "case_id": result["case_id"],
                "variant": result["variant"],
                "provider": (
                    f"cognitive-powers-{plugin_version}"
                    if result["variant"] == "candidate"
                    else "codex-base"
                ),
                "task": args.task_id,
                "success": result["success"],
                "critical_errors": result["critical_errors"],
                "quality_score": result["quality_score"],
                "input_tokens": result["input_tokens"],
                "output_tokens": result["output_tokens"],
                "elapsed_seconds": result["elapsed_seconds"],
                "evidence": [
                    f"hidden exit {result['hidden_exit']}",
                    "changed " + ",".join(result["changed_paths"]),
                    *result["quality_evidence"],
                ],
                "live_execution": True,
            }
            if binding is not None:
                receipt.update(
                    {
                        **binding,
                        "task_id": args.task_id,
                        "model": args.model,
                        "reasoning_effort": args.reasoning_effort,
                        "prompt": args.prompt,
                        "repetition": result["repetition"],
                        "source_sha256": result["source_sha256"],
                        "tools": ["shell_tool"],
                        "permissions": [
                            "dangerously-bypass-approvals-and-sandbox"
                            if args.bypass_sandbox
                            else "workspace-write"
                        ],
                        "arm_order": result["arm_order"],
                        "independent_tests_passed": result["hidden_exit"] == 0,
                        "turns": 1,
                        "tool_calls": result["tool_calls"],
                        "retries": 0,
                        "hidden_check_sha256": hidden_identity["sha256"],
                        "quality_check_sha256": quality_identity["sha256"],
                    }
                )
            receipts.append(receipt)
        (output / "results.json").write_text(
            json.dumps(results, indent=2) + "\n", encoding="utf-8"
        )
        (output / "receipts.json").write_text(
            json.dumps(receipts, indent=2) + "\n", encoding="utf-8"
        )
        summary = {
            "schema_version": 2 if binding is not None else 1,
            "task_id": args.task_id,
            "task_binding": binding,
            "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "seed": args.seed,
            "repetitions": args.repetitions,
            "source_sha256": fixture_sha,
            "plugin_version": plugin_version,
            "candidate_plugin": plugin_identity,
            "candidate_plugin_postflight": postflight_plugin_identity,
            "hidden_check": hidden_identity,
            "quality_check": quality_identity,
            "guarded_roots": guard_receipts,
            "all_runs_successful": all(result["success"] for result in results),
            "comparison": aggregate_results(results),
            "end_to_end_improvement_proven": False,
            "results": str(output / "results.json"),
            "receipts": str(output / "receipts.json"),
        }
        (output / "summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if summary["all_runs_successful"] else 1
    except (OSError, LiveEvaluationError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
