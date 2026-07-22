#!/usr/bin/env python3
"""Define, materialize, and validate the controller A/B fixture corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = (
    PLUGIN_ROOT / "benchmarks" / "confirmatory" / "controller_ab_corpus.json"
)
MODES = ("solo", "parallel-read-only", "parallel-packets", "staged-verify")
CATEGORIES = (
    "bug-fix",
    "multi-file-implementation",
    "current-source-research",
    "delivery-verification",
    "real-host-interaction",
)
SPLIT_COUNTS = {"pilot": 1, "promotion": 3}
IGNORED_PARTS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache"}


class CorpusError(ValueError):
    """Raised when a corpus definition or materialization is unsafe or invalid."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_relative(value: str, field: str) -> str:
    normalized = value.replace("\\", "/").strip().rstrip("/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or ":" in path.parts[0]
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise CorpusError(f"{field} must be a safe relative path")
    return str(path)


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise CorpusError("manifest must be a schema_version 1 object")
    if value.get("contains_run_results") is not False:
        raise CorpusError("fixture manifest cannot contain run results")
    return value


def _actor_payload(definition: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "corpus_id": definition["corpus_id"],
        "fixture_id": definition["fixture_id"],
        "category": definition["category"],
        "prompt": definition["prompt"],
        "goal": definition["goal"],
        "allowed_paths": definition["allowed_paths"],
        "public_check": ["python", "-m", "unittest", "tests.test_public"],
        "repetitions_per_arm": definition["repetitions_per_arm"],
    }


def actor_payload(definition: dict[str, Any]) -> dict[str, Any]:
    """Return the only payload that may be handed to an acting arm."""
    payload = _actor_payload(definition)
    forbidden = {"expected_mode", "evaluation", "hidden_check", "quality_check"}

    def visit(value: object) -> None:
        if isinstance(value, dict):
            if forbidden.intersection(value):
                raise CorpusError("actor payload contains evaluator-only fields")
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return payload


def _goal(mode: str, fixture_id: str) -> str:
    if mode == "solo":
        return f"Set src/scenario.txt to resolved:{fixture_id}."
    if mode == "parallel-read-only":
        return "Report both immutable evidence markers without changing the checkout."
    if mode == "parallel-packets":
        return (
            f"Set packets/a/state.txt to completed:{fixture_id}:a and "
            f"packets/b/state.txt to completed:{fixture_id}:b."
        )
    return f"Set src/scenario.txt to verified:{fixture_id} and prove it with tests."


def _render_actor_files(definition: dict[str, Any]) -> dict[str, bytes]:
    fixture_id = definition["fixture_id"]
    mode = definition["expected_mode"]
    payload = actor_payload(definition)
    initial_scenario = (
        f"EVIDENCE-A:{fixture_id}\nEVIDENCE-B:{fixture_id}\n"
        if mode == "parallel-read-only"
        else f"pending:{fixture_id}\n"
    )
    test_source = """import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PublicFixtureTest(unittest.TestCase):
    def test_declared_outcome(self):
        task = json.loads((ROOT / "task.json").read_text(encoding="utf-8"))
        self.assertNotIn("expected_mode", json.dumps(task))
        fixture_id = task["fixture_id"]
        allowed = task["allowed_paths"]
        if allowed == []:
            text = (ROOT / "src" / "scenario.txt").read_text(encoding="utf-8")
            self.assertIn("EVIDENCE-A:" + fixture_id, text)
            self.assertIn("EVIDENCE-B:" + fixture_id, text)
        elif "packets/a/**" in allowed:
            self.assertEqual((ROOT / "packets/a/state.txt").read_text(encoding="utf-8").strip(), "completed:" + fixture_id + ":a")
            self.assertEqual((ROOT / "packets/b/state.txt").read_text(encoding="utf-8").strip(), "completed:" + fixture_id + ":b")
        elif "tests/**" in allowed:
            self.assertEqual((ROOT / "src/scenario.txt").read_text(encoding="utf-8").strip(), "verified:" + fixture_id)
        else:
            self.assertEqual((ROOT / "src/scenario.txt").read_text(encoding="utf-8").strip(), "resolved:" + fixture_id)


if __name__ == "__main__":
    unittest.main()
"""
    return {
        "README.md": (
            f"# Controller A/B fixture {fixture_id}\n\n"
            "This is a deterministic evaluation fixture definition, not a run result.\n"
        ).encode(),
        "task.json": (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode(),
        "src/scenario.txt": initial_scenario.encode(),
        "packets/a/state.txt": f"pending:{fixture_id}:a\n".encode(),
        "packets/b/state.txt": f"pending:{fixture_id}:b\n".encode(),
        "tests/__init__.py": b"",
        "tests/test_public.py": test_source.encode(),
    }


def _render_evaluator_files(definition: dict[str, Any]) -> dict[str, bytes]:
    hidden = """import json
import subprocess
import sys
from pathlib import Path

fixture = Path(sys.argv[1]).resolve()
task = json.loads((fixture / "task.json").read_text(encoding="utf-8"))
if "expected_mode" in json.dumps(task):
    raise SystemExit(3)
completed = subprocess.run(
    [sys.executable, "-m", "unittest", "tests.test_public"],
    cwd=fixture,
    check=False,
)
raise SystemExit(completed.returncode)
"""
    quality = """import json
import subprocess
import sys
from pathlib import Path

fixture = Path(sys.argv[1]).resolve()
message = Path(sys.argv[2]) if len(sys.argv) > 2 else None
completed = subprocess.run(
    [sys.executable, "-m", "unittest", "tests.test_public"],
    cwd=fixture,
    check=False,
    capture_output=True,
    text=True,
)
score = 100.0 if completed.returncode == 0 else 0.0
evidence = ["independent public-contract check"]
if message is not None and not message.exists():
    score = 0.0
    evidence.append("final message missing")
print(json.dumps({"score": score, "evidence": evidence, "critical_errors": []}))
"""
    return {"hidden_check.py": hidden.encode(), "quality_check.py": quality.encode()}


def _files_sha256(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path, content in sorted(files.items()):
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(_sha256(content).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def expand_definitions(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    dimensions = manifest.get("dimensions", {})
    if tuple(dimensions.get("modes", [])) != MODES:
        raise CorpusError("manifest must contain exactly the four controller modes")
    if tuple(dimensions.get("categories", [])) != CATEGORIES:
        raise CorpusError("manifest must contain exactly the five categories")
    if manifest.get("repetitions_per_arm") != 3:
        raise CorpusError("confirmatory fixtures require three repetitions per arm")
    definitions: list[dict[str, Any]] = []
    for split, count in SPLIT_COUNTS.items():
        split_config = manifest.get("splits", {}).get(split)
        if (
            not isinstance(split_config, dict)
            or split_config.get("fixtures_per_cell") != count
        ):
            raise CorpusError(f"{split} must define {count} fixtures per matrix cell")
        for mode in MODES:
            for category in CATEGORIES:
                for ordinal in range(1, count + 1):
                    fixture_id = f"controller-{split}-{mode}-{category}-{ordinal:02d}"
                    definition = {
                        "schema_version": 1,
                        "corpus_id": manifest["corpus_id"],
                        "fixture_id": fixture_id,
                        "split": split,
                        "held_out": split == "promotion",
                        "category": category,
                        "expected_mode": mode,
                        "ordinal": ordinal,
                        "repetitions_per_arm": 3,
                        "prompt": manifest["category_prompts"][category],
                        "goal": _goal(mode, fixture_id),
                        "allowed_paths": manifest["mode_recipes"][mode][
                            "allowed_paths"
                        ],
                        "actor_path": _safe_relative(
                            f"{split_config['actor_root']}/{fixture_id}", "actor_path"
                        ),
                        "evaluator_path": _safe_relative(
                            f"{split_config['evaluator_root']}/{fixture_id}",
                            "evaluator_path",
                        ),
                        "checks": {
                            "public": ["python", "-m", "unittest", "tests.test_public"],
                            "hidden": ["python", "hidden_check.py", "{fixture}"],
                            "quality": [
                                "python",
                                "quality_check.py",
                                "{fixture}",
                                "{message}",
                            ],
                        },
                        "git_identity": {
                            "required": True,
                            "branch": manifest["recipe"]["git_default_branch"],
                            "author_name": manifest["recipe"]["git_author_name"],
                            "author_email": manifest["recipe"]["git_author_email"],
                            "timestamp": manifest["recipe"]["git_timestamp"],
                            "clean": True,
                        },
                    }
                    actor_files = _render_actor_files(definition)
                    evaluator_files = _render_evaluator_files(definition)
                    definition["expected_hashes"] = {
                        "actor_source_sha256": _files_sha256(actor_files),
                        "hidden_check_sha256": _sha256(
                            evaluator_files["hidden_check.py"]
                        ),
                        "quality_check_sha256": _sha256(
                            evaluator_files["quality_check.py"]
                        ),
                    }
                    definition["definition_id"] = "definition-" + _sha256(
                        _canonical(definition)
                    )
                    definitions.append(definition)
    if (
        len(definitions) != 80
        or len({item["fixture_id"] for item in definitions}) != 80
    ):
        raise CorpusError("expanded corpus must contain exactly 80 unique fixtures")
    return definitions


def promotion_definition_seal(definitions: list[dict[str, Any]]) -> str:
    promotion = [item for item in definitions if item["split"] == "promotion"]
    return _sha256(_canonical(promotion))


def build_task_contract(manifest: dict[str, Any]) -> dict[str, Any]:
    """Build the 80-fixture task contract consumed by the live runner."""
    definitions = expand_definitions(manifest)
    tasks: list[dict[str, Any]] = []
    for definition in definitions:
        prompt = f"{definition['prompt']}\n\nObjective: {definition['goal']}"
        tasks.append(
            {
                "task_id": definition["fixture_id"],
                "version": 1,
                "split": definition["split"],
                "category": definition["category"],
                "fixture_id": definition["fixture_id"],
                "expected_mode": definition["expected_mode"],
                "prompt": prompt,
                "initial_state": (
                    "A clean, versioned, deterministic fixture with evaluator files "
                    "held outside the acting checkout."
                ),
                "expected_outcome": definition["goal"],
                "independent_tests": [
                    "Run the fixture public test inside the acting checkout.",
                    "Run the external hidden and quality checks on independent clones.",
                ],
                "critical_failures": [
                    "The objective is incomplete or contradicted by the hidden check.",
                    "The acting arm changes a path outside the declared ownership.",
                    "The acting arm reads or modifies evaluator-only content.",
                ],
                "budgets": {
                    "max_total_tokens": 120000,
                    "max_elapsed_seconds": 1800,
                },
            }
        )
    rounds: dict[str, Any] = {}
    for split, seed in (
        ("pilot", "cp-controller-pilot-2026-07-v1"),
        ("promotion", "cp-controller-promotion-2026-07-v1"),
    ):
        rounds[split] = {
            "held_out": split == "promotion",
            "task_ids": [item["task_id"] for item in tasks if item["split"] == split],
            "repetitions_per_task": 3,
            "arm_order": {"method": "seeded-balanced", "seed": seed},
        }
    return {
        "schema_version": 2,
        "task_set_id": "controller-ab-confirmatory-80-v1",
        "contains_run_results": False,
        "controller_confirmatory_corpus": {
            "manifest": "benchmarks/confirmatory/controller_ab_corpus.json",
            "fixture_count": 80,
            "pilot_fixture_count": 20,
            "promotion_fixture_count": 60,
            "session_count": 480,
            "repetitions_per_arm": 3,
            "contains_run_results": False,
            "end_to_end_improvement_proven": False,
            "definition_seal_sha256": promotion_definition_seal(definitions),
        },
        "protocol": {
            "locked_between_arms": [
                "model",
                "reasoning_effort",
                "prompt",
                "tools",
                "permissions",
                "fixture_id",
                "source_sha256",
            ],
            "quality_before_efficiency": True,
            "reject_critical_failures": True,
            "minimum_average_quality_delta": 0.05,
        },
        "rounds": rounds,
        "tasks": tasks,
    }


def build_batch_config(
    manifest: dict[str, Any],
    *,
    materialized_root: Path,
    task_contract: Path,
    controller_protocol: Path,
    baseline_home: Path,
    candidate_home: Path,
    model: str,
    reasoning_effort: str,
    bypass_sandbox: bool = False,
) -> dict[str, Any]:
    report = validate_corpus(manifest, materialized_root)
    if (
        report.get("fixture_status") != "ready"
        or report.get("ready_fixture_count") != 80
    ):
        raise CorpusError("batch configuration requires all 80 materialized fixtures")
    definitions = expand_definitions(manifest)
    tasks: dict[str, Any] = {}
    for definition in definitions:
        evaluator = materialized_root / definition["evaluator_path"]
        allowed = definition["allowed_paths"] or ["__read_only_no_changes__"]
        tasks[definition["fixture_id"]] = {
            "fixture": str((materialized_root / definition["actor_path"]).resolve()),
            "hidden_check": [
                "py",
                "-3",
                str((evaluator / "hidden_check.py").resolve()),
                "{fixture}",
            ],
            "quality_check": [
                "py",
                "-3",
                str((evaluator / "quality_check.py").resolve()),
                "{fixture}",
                "{message}",
            ],
            "allow_changes": allowed,
            "guard_roots": [str(evaluator.resolve())],
        }
    return {
        "schema_version": 1,
        "task_contract": str(task_contract.resolve()),
        "controller_protocol": str(controller_protocol.resolve()),
        "baseline_home": str(baseline_home.resolve()),
        "candidate_home": str(candidate_home.resolve()),
        "model": model,
        "reasoning_effort": reasoning_effort,
        "available_tools": [
            "shell_command",
            "apply_patch",
            "spawn_agent",
            "wait_agent",
            "send_message",
        ],
        "agent_slots": 4,
        "codex": "codex",
        "bypass_sandbox": bypass_sandbox,
        "tasks": tasks,
    }


def _tree_files(root: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in IGNORED_PARTS for part in path.parts):
            continue
        result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def _run_git(root: Path, *arguments: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if completed.returncode != 0:
        raise CorpusError(
            f"git {' '.join(arguments)} failed for {root}: {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def git_identity(root: Path) -> dict[str, Any]:
    top = Path(_run_git(root, "rev-parse", "--show-toplevel")).resolve()
    if top != root.resolve():
        raise CorpusError("actor fixture is not the root of its Git checkout")
    head = _run_git(root, "rev-parse", "HEAD")
    branch = _run_git(root, "branch", "--show-current")
    status = _run_git(root, "status", "--porcelain=v1", "--untracked-files=all")
    author = _run_git(root, "show", "-s", "--format=%an%n%ae%n%aI", "HEAD").splitlines()
    identity = {
        "head": head,
        "branch": branch,
        "status_sha256": _sha256(status.encode()),
        "clean": status == "",
        "author_name": author[0] if len(author) > 0 else "",
        "author_email": author[1] if len(author) > 1 else "",
        "author_timestamp": author[2] if len(author) > 2 else "",
    }
    identity["identity_sha256"] = _sha256(_canonical(identity))
    return identity


def _write_files(root: Path, files: dict[str, bytes]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def materialize(manifest: dict[str, Any], output_root: Path) -> dict[str, Any]:
    if not output_root.is_absolute():
        raise CorpusError("materialization output must be an explicit absolute path")
    resolved = output_root.resolve()
    if (
        resolved == PLUGIN_ROOT
        or PLUGIN_ROOT in resolved.parents
        or resolved in PLUGIN_ROOT.parents
    ):
        raise CorpusError(
            "materialization output must be outside the plugin repository"
        )
    if resolved.exists() and any(resolved.iterdir()):
        raise CorpusError("materialization output must not already contain files")
    resolved.mkdir(parents=True, exist_ok=True)
    definitions = expand_definitions(manifest)
    locks: list[dict[str, Any]] = []
    recipe = manifest["recipe"]
    for definition in definitions:
        actor_root = resolved / definition["actor_path"]
        evaluator_root = resolved / definition["evaluator_path"]
        actor_root.mkdir(parents=True)
        evaluator_root.mkdir(parents=True)
        actor_files = _render_actor_files(definition)
        evaluator_files = _render_evaluator_files(definition)
        _write_files(actor_root, actor_files)
        _write_files(evaluator_root, evaluator_files)
        _run_git(actor_root, "init", "-b", recipe["git_default_branch"])
        _run_git(actor_root, "config", "core.autocrlf", "false")
        _run_git(actor_root, "config", "user.name", recipe["git_author_name"])
        _run_git(actor_root, "config", "user.email", recipe["git_author_email"])
        _run_git(actor_root, "add", "--all")
        env = os.environ.copy()
        env["GIT_AUTHOR_DATE"] = recipe["git_timestamp"]
        env["GIT_COMMITTER_DATE"] = recipe["git_timestamp"]
        _run_git(
            actor_root,
            "commit",
            "-m",
            f"Initialize {definition['fixture_id']}",
            env=env,
        )
        locks.append(
            {
                "fixture_id": definition["fixture_id"],
                "definition_id": definition["definition_id"],
                "actor_source_sha256": _files_sha256(_tree_files(actor_root)),
                "hidden_check_sha256": _sha256(
                    (evaluator_root / "hidden_check.py").read_bytes()
                ),
                "quality_check_sha256": _sha256(
                    (evaluator_root / "quality_check.py").read_bytes()
                ),
                "git_identity": git_identity(actor_root),
            }
        )
    promotion_locks = [
        item for item in locks if item["fixture_id"].startswith("controller-promotion-")
    ]
    lock = {
        "schema_version": 1,
        "kind": "controller_ab_fixture_lock",
        "corpus_id": manifest["corpus_id"],
        "contains_run_results": False,
        "definition_seal_sha256": promotion_definition_seal(definitions),
        "promotion_content_seal_sha256": _sha256(_canonical(promotion_locks)),
        "fixtures": locks,
    }
    (resolved / "corpus-lock.json").write_text(
        json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return lock


def validate_corpus(
    manifest: dict[str, Any], materialized_root: Path | None = None
) -> dict[str, Any]:
    definitions = expand_definitions(manifest)
    seal = promotion_definition_seal(definitions)
    promotion = manifest["splits"]["promotion"]
    contract_errors: list[str] = []
    if promotion.get("definition_sealed") is not True:
        contract_errors.append("promotion definitions are not sealed")
    if promotion.get("definition_seal_sha256") != seal:
        contract_errors.append("promotion definition seal does not match")
    cell_counts: dict[str, int] = {}
    for item in definitions:
        key = f"{item['split']}|{item['expected_mode']}|{item['category']}"
        cell_counts[key] = cell_counts.get(key, 0) + 1
    expected_cells = {
        f"{split}|{mode}|{category}": count
        for split, count in SPLIT_COUNTS.items()
        for mode in MODES
        for category in CATEGORIES
    }
    if cell_counts != expected_cells:
        contract_errors.append("fixture matrix is not exactly 1/3 per split cell")

    statuses: list[dict[str, Any]] = []
    lock_by_id: dict[str, Any] = {}
    root = materialized_root.resolve() if materialized_root is not None else None
    if root is not None:
        lock_path = root / "corpus-lock.json"
        if not lock_path.is_file():
            contract_errors.append("materialized corpus lock is missing")
        else:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            if lock.get("contains_run_results") is not False:
                contract_errors.append("corpus lock cannot contain run results")
            if lock.get("definition_seal_sha256") != seal:
                contract_errors.append("materialized definition seal does not match")
            lock_by_id = {
                item.get("fixture_id"): item
                for item in lock.get("fixtures", [])
                if isinstance(item, dict)
            }
            promotion_locks = [
                lock_by_id[item["fixture_id"]]
                for item in definitions
                if item["split"] == "promotion" and item["fixture_id"] in lock_by_id
            ]
            if lock.get("promotion_content_seal_sha256") != _sha256(
                _canonical(promotion_locks)
            ):
                contract_errors.append("promotion content seal does not match")

    for definition in definitions:
        reasons: list[str] = []
        if root is None:
            reasons.append("fixture directory has not been materialized")
        else:
            actor_root = root / definition["actor_path"]
            evaluator_root = root / definition["evaluator_path"]
            fixture_lock = lock_by_id.get(definition["fixture_id"])
            if not actor_root.is_dir() or not evaluator_root.is_dir():
                reasons.append("actor or evaluator directory is missing")
            elif fixture_lock is None:
                reasons.append("fixture lock is missing")
            else:
                actual_source = _files_sha256(_tree_files(actor_root))
                expected = definition["expected_hashes"]
                if actual_source != expected["actor_source_sha256"]:
                    reasons.append("actor source hash differs from recipe")
                if actual_source != fixture_lock.get("actor_source_sha256"):
                    reasons.append("actor source hash differs from lock")
                for name in ("hidden_check", "quality_check"):
                    path = evaluator_root / f"{name}.py"
                    actual = _sha256(path.read_bytes()) if path.is_file() else None
                    if actual != expected[f"{name}_sha256"]:
                        reasons.append(f"{name} hash differs from recipe")
                    if actual != fixture_lock.get(f"{name}_sha256"):
                        reasons.append(f"{name} hash differs from lock")
                try:
                    identity = git_identity(actor_root)
                except CorpusError as error:
                    reasons.append(str(error))
                else:
                    expected_git = definition["git_identity"]
                    if not identity["clean"]:
                        reasons.append("Git checkout is not clean")
                    for field in ("branch", "author_name", "author_email"):
                        if identity[field] != expected_git[field]:
                            reasons.append(f"Git {field} differs from recipe")
                    if identity != fixture_lock.get("git_identity"):
                        reasons.append("Git identity differs from lock")
                task_path = actor_root / "task.json"
                try:
                    actual_payload = json.loads(task_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    reasons.append("actor task payload is missing or invalid")
                else:
                    if actual_payload != actor_payload(definition):
                        reasons.append("actor task payload differs from definition")
                    if "expected_mode" in json.dumps(actual_payload):
                        reasons.append("actor task payload leaks expected_mode")
        statuses.append(
            {
                "fixture_id": definition["fixture_id"],
                "split": definition["split"],
                "category": definition["category"],
                "expected_mode": definition["expected_mode"],
                "definition_id": definition["definition_id"],
                "fixture_status": "ready" if not reasons else "pending",
                "reasons": reasons,
            }
        )
    ready = sum(item["fixture_status"] == "ready" for item in statuses)
    return {
        "schema_version": 1,
        "kind": "controller_ab_fixture_validation",
        "corpus_id": manifest["corpus_id"],
        "contract_valid": not contract_errors,
        "contract_errors": contract_errors,
        "fixture_count": len(definitions),
        "pilot_fixture_count": sum(item["split"] == "pilot" for item in definitions),
        "promotion_fixture_count": sum(
            item["split"] == "promotion" for item in definitions
        ),
        "repetitions_per_arm": manifest["repetitions_per_arm"],
        "ready_fixture_count": ready,
        "fixture_status": "ready" if ready == 80 and not contract_errors else "pending",
        "contains_run_results": False,
        "end_to_end_improvement_proven": False,
        "promotion_definition_seal_sha256": seal,
        "fixtures": statuses,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--materialized-root", type=Path)
    materialize_parser = subparsers.add_parser("materialize")
    materialize_parser.add_argument("--output-root", type=Path, required=True)
    actor_parser = subparsers.add_parser("actor-payload")
    actor_parser.add_argument("--fixture-id", required=True)
    subparsers.add_parser("definition-seal")
    task_contract_parser = subparsers.add_parser("write-task-contract")
    task_contract_parser.add_argument("--output", type=Path, required=True)
    batch_parser = subparsers.add_parser("write-batch-config")
    batch_parser.add_argument("--materialized-root", type=Path, required=True)
    batch_parser.add_argument("--task-contract", type=Path, required=True)
    batch_parser.add_argument("--controller-protocol", type=Path, required=True)
    batch_parser.add_argument("--baseline-home", type=Path, required=True)
    batch_parser.add_argument("--candidate-home", type=Path, required=True)
    batch_parser.add_argument("--model", required=True)
    batch_parser.add_argument("--reasoning-effort", required=True)
    batch_parser.add_argument(
        "--bypass-sandbox",
        action="store_true",
        help="grant both frozen arms identical unsandboxed shell permissions",
    )
    batch_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = load_manifest(args.manifest)
        if args.command == "materialize":
            output = materialize(manifest, args.output_root)
        elif args.command == "validate":
            output = validate_corpus(manifest, args.materialized_root)
        elif args.command == "definition-seal":
            output = {
                "definition_seal_sha256": promotion_definition_seal(
                    expand_definitions(manifest)
                )
            }
        elif args.command == "write-task-contract":
            output = build_task_contract(manifest)
            args.output.write_text(
                json.dumps(output, indent=2, sort_keys=False) + "\n", encoding="utf-8"
            )
            output = {
                "output": str(args.output.resolve()),
                "task_count": len(output["tasks"]),
                "sha256": _sha256(args.output.read_bytes()),
            }
        elif args.command == "write-batch-config":
            output = build_batch_config(
                manifest,
                materialized_root=args.materialized_root.resolve(),
                task_contract=args.task_contract,
                controller_protocol=args.controller_protocol,
                baseline_home=args.baseline_home,
                candidate_home=args.candidate_home,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                bypass_sandbox=args.bypass_sandbox,
            )
            args.output.write_text(
                json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            output = {
                "output": str(args.output.resolve()),
                "task_count": len(output["tasks"]),
                "sha256": _sha256(args.output.read_bytes()),
            }
        else:
            matches = [
                item
                for item in expand_definitions(manifest)
                if item["fixture_id"] == args.fixture_id
            ]
            if len(matches) != 1:
                raise CorpusError("fixture_id does not identify exactly one fixture")
            output = actor_payload(matches[0])
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    except (CorpusError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
