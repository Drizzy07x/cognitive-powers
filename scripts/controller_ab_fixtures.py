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
CORPUS_SCHEMA_VERSION = 2
FIXTURE_SCHEMA_VERSION = 2
TASK_CONTRACT_SCHEMA_VERSION = 3
IGNORED_PARTS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache"}
OPAQUE_FIXTURE_ID_PREFIX = "cpfx-"
ACTOR_FORBIDDEN_FIELDS = {
    "actor_path",
    "category",
    "checks",
    "evaluation",
    "evaluator_path",
    "expected_mode",
    "held_out",
    "hidden_check",
    "ordinal",
    "quality_check",
    "split",
}


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
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != CORPUS_SCHEMA_VERSION
    ):
        raise CorpusError("manifest must be a schema_version 2 object")
    if value.get("contains_run_results") is not False:
        raise CorpusError("fixture manifest cannot contain run results")
    return value


def _actor_payload(definition: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "corpus_id": definition["corpus_id"],
        "fixture_id": definition["fixture_id"],
        "prompt": definition["prompt"],
        "goal": definition["goal"],
        "allowed_paths": definition["allowed_paths"],
        "work_contract": definition["work_contract"],
        "verification": definition["verification"],
        "public_check": ["python", "-m", "unittest", "tests.test_public"],
        "repetitions_per_arm": definition["repetitions_per_arm"],
    }


def actor_payload(definition: dict[str, Any]) -> dict[str, Any]:
    """Return the only payload that may be handed to an acting arm."""
    payload = _actor_payload(definition)

    def visit(value: object) -> None:
        if isinstance(value, dict):
            if ACTOR_FORBIDDEN_FIELDS.intersection(value):
                raise CorpusError("actor payload contains evaluator-only fields")
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    fixture_id = payload.get("fixture_id")
    if (
        not isinstance(fixture_id, str)
        or not fixture_id.startswith(OPAQUE_FIXTURE_ID_PREFIX)
        or any(
            label in fixture_id.casefold()
            for label in (*MODES, *CATEGORIES, *SPLIT_COUNTS)
        )
    ):
        raise CorpusError("actor payload requires an opaque fixture_id")
    return payload


def _opaque_fixture_id(
    corpus_id: str, split: str, mode: str, category: str, ordinal: int
) -> str:
    """Return a stable identifier whose text discloses no experiment labels."""
    identity = "\0".join((corpus_id, split, mode, category, str(ordinal)))
    return OPAQUE_FIXTURE_ID_PREFIX + _sha256(identity.encode("utf-8"))[:24]


def _goal(mode: str, fixture_id: str) -> str:
    if mode == "solo":
        return (
            "Resolve the single bounded discrepancy in src/target.txt from the "
            "local specification, then run the cheap public check."
        )
    if mode == "parallel-read-only":
        return (
            "Independently inspect evidence/unit-a.txt and evidence/unit-b.txt, then "
            "report both RESULT markers without changing the checkout."
        )
    if mode == "parallel-packets":
        return (
            "Complete the two independent packet outputs under work/a and work/b, "
            "integrate their results, and have a distinct verifier run the check."
        )
    return (
        "Correct src/target.txt, then have a fresh read-only verifier execute "
        "verification/verify.py after the change is integrated."
    )


def _scenario_value(category: str, fixture_id: str, unit: str = "target") -> str:
    outcomes = {
        "bug-fix": "defect=fixed",
        "multi-file-implementation": "feature=integrated",
        "current-source-research": "decision=evidence-backed",
        "delivery-verification": "delivery=verified",
        "real-host-interaction": "workflow=completed",
    }
    return f"{outcomes[category]};unit={unit};fixture={fixture_id}"


def _work_contract(mode: str) -> dict[str, Any]:
    if mode == "solo":
        return {
            "units": [
                {
                    "id": "bounded-fix",
                    "ownership": ["src/target.txt"],
                    "read_only": False,
                    "depends_on": [],
                }
            ],
            "independence": "one bounded unit",
            "coordination_budget": "complete locally when cheaper than delegation",
        }
    if mode == "parallel-read-only":
        return {
            "units": [
                {
                    "id": "evidence-a",
                    "ownership": ["evidence/unit-a.txt"],
                    "read_only": True,
                    "depends_on": [],
                },
                {
                    "id": "evidence-b",
                    "ownership": ["evidence/unit-b.txt"],
                    "read_only": True,
                    "depends_on": [],
                },
            ],
            "independence": "two independent read-only investigations",
            "coordination_budget": "parallel investigation is permitted",
        }
    if mode == "parallel-packets":
        return {
            "units": [
                {
                    "id": "packet-a",
                    "ownership": ["work/a/result.txt"],
                    "read_only": False,
                    "depends_on": [],
                },
                {
                    "id": "packet-b",
                    "ownership": ["work/b/result.txt"],
                    "read_only": False,
                    "depends_on": [],
                },
            ],
            "independence": "two disjoint writable packets",
            "coordination_budget": "parallel execution followed by primary integration",
        }
    return {
        "units": [
            {
                "id": "executor",
                "ownership": ["src/target.txt"],
                "read_only": False,
                "depends_on": [],
            },
            {
                "id": "verification",
                "ownership": [],
                "read_only": True,
                "depends_on": ["executor"],
            },
        ],
        "independence": "ordered executor then distinct read-only verifier",
        "coordination_budget": "verification must run in a later wave",
    }


def _validate_structural_definition(definition: dict[str, Any]) -> None:
    mode = definition["expected_mode"]
    units = definition["work_contract"]["units"]
    allowed = definition["allowed_paths"]
    verification = definition["verification"]
    if mode == "solo":
        if len(units) != 1 or units[0]["read_only"] or len(allowed) != 1:
            raise CorpusError("solo fixtures require one bounded writable unit")
    elif mode == "parallel-read-only":
        if len(units) != 2 or not all(unit["read_only"] for unit in units) or allowed:
            raise CorpusError("parallel read-only fixtures require two immutable units")
    elif mode == "parallel-packets":
        ownership = [tuple(unit["ownership"]) for unit in units]
        if (
            len(units) != 2
            or any(unit["read_only"] for unit in units)
            or not all(not unit["depends_on"] for unit in units)
            or len(set(ownership)) != 2
            or set(allowed) != {"work/a/result.txt", "work/b/result.txt"}
            or verification["distinct_verifier_required"] is not True
        ):
            raise CorpusError(
                "parallel packet fixtures require two disjoint writes and a verifier"
            )
    else:
        if (
            len(units) != 2
            or units[0]["read_only"]
            or not units[1]["read_only"]
            or units[1]["depends_on"] != [units[0]["id"]]
            or verification["executable_check"] != ["python", "verification/verify.py"]
            or verification["distinct_verifier_required"] is not True
        ):
            raise CorpusError(
                "staged fixtures require an executor and later executable verification"
            )


def _render_actor_files(definition: dict[str, Any]) -> dict[str, bytes]:
    fixture_id = definition["fixture_id"]
    category = definition["category"]
    payload = actor_payload(definition)
    expected_target = _scenario_value(category, fixture_id)
    evidence_a = f"RESULT-A:{_sha256((fixture_id + ':a').encode())[:16]}"
    evidence_b = f"RESULT-B:{_sha256((fixture_id + ':b').encode())[:16]}"
    test_source = """import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PublicFixtureTest(unittest.TestCase):
    def test_declared_outcome(self):
        task = json.loads((ROOT / "task.json").read_text(encoding="utf-8"))
        self.assertNotIn("expected_mode", json.dumps(task))
        units = task["work_contract"]["units"]
        if all(unit["read_only"] for unit in units):
            self.assertTrue((ROOT / "evidence/unit-a.txt").is_file())
            self.assertTrue((ROOT / "evidence/unit-b.txt").is_file())
        elif len(units) == 2 and not units[0]["read_only"] and not units[1]["read_only"]:
            for packet in ("a", "b"):
                expected = (ROOT / f"work/{packet}/expected.txt").read_text(encoding="utf-8").strip()
                actual = (ROOT / f"work/{packet}/result.txt").read_text(encoding="utf-8").strip()
                self.assertEqual(actual, expected)
        else:
            expected = (ROOT / "spec/expected.txt").read_text(encoding="utf-8").strip()
            self.assertEqual((ROOT / "src/target.txt").read_text(encoding="utf-8").strip(), expected)


if __name__ == "__main__":
    unittest.main()
"""
    verifier = """from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
actual = (root / "src/target.txt").read_text(encoding="utf-8").strip()
expected = (root / "spec/expected.txt").read_text(encoding="utf-8").strip()
raise SystemExit(0 if actual == expected else 1)
"""
    return {
        "README.md": (
            f"# Controller A/B fixture {fixture_id}\n\n"
            "This is a deterministic evaluation fixture definition, not a run result.\n"
        ).encode(),
        "task.json": (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode(),
        "src/target.txt": f"pending:{fixture_id}\n".encode(),
        "spec/expected.txt": f"{expected_target}\n".encode(),
        "evidence/unit-a.txt": f"{evidence_a}\n".encode(),
        "evidence/unit-b.txt": f"{evidence_b}\n".encode(),
        "work/a/input.txt": f"source:{fixture_id}:a\n".encode(),
        "work/a/expected.txt": (
            _scenario_value(category, fixture_id, "packet-a") + "\n"
        ).encode(),
        "work/a/result.txt": f"pending:{fixture_id}:a\n".encode(),
        "work/b/input.txt": f"source:{fixture_id}:b\n".encode(),
        "work/b/expected.txt": (
            _scenario_value(category, fixture_id, "packet-b") + "\n"
        ).encode(),
        "work/b/result.txt": f"pending:{fixture_id}:b\n".encode(),
        "verification/verify.py": verifier.encode(),
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
critical_errors = []
if message is None or not message.exists():
    score = 0.0
    evidence.append("final message missing")
else:
    task = json.loads((fixture / "task.json").read_text(encoding="utf-8"))
    units = task["work_contract"]["units"]
    if all(unit["read_only"] for unit in units):
        response = message.read_text(encoding="utf-8", errors="replace")
        markers = [
            (fixture / "evidence/unit-a.txt").read_text(encoding="utf-8").strip(),
            (fixture / "evidence/unit-b.txt").read_text(encoding="utf-8").strip(),
        ]
        missing = [marker for marker in markers if marker not in response]
        if missing:
            score = 0.0
            critical_errors.append("independent evidence markers missing")
        else:
            evidence.append("both independent evidence markers reported")
print(json.dumps({"score": score, "evidence": evidence, "critical_errors": critical_errors}))
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
                    fixture_id = _opaque_fixture_id(
                        manifest["corpus_id"], split, mode, category, ordinal
                    )
                    definition = {
                        "schema_version": FIXTURE_SCHEMA_VERSION,
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
                        "work_contract": _work_contract(mode),
                        "verification": {
                            "public_check": [
                                "python",
                                "-m",
                                "unittest",
                                "tests.test_public",
                            ],
                            "executable_check": (
                                ["python", "verification/verify.py"]
                                if mode == "staged-verify"
                                else [
                                    "python",
                                    "-m",
                                    "unittest",
                                    "tests.test_public",
                                ]
                            ),
                            "distinct_verifier_required": mode
                            in {"parallel-packets", "staged-verify"},
                        },
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
                    _validate_structural_definition(definition)
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
                "version": FIXTURE_SCHEMA_VERSION,
                "split": definition["split"],
                "category": definition["category"],
                "fixture_id": definition["fixture_id"],
                "expected_mode": definition["expected_mode"],
                "prompt": prompt,
                "initial_state": (
                    "A clean, versioned, deterministic fixture with a bounded public "
                    "contract."
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
        ("pilot", "cp-controller-pilot-2026-07-v2"),
        ("promotion", "cp-controller-promotion-2026-07-v2"),
    ):
        rounds[split] = {
            "held_out": split == "promotion",
            "task_ids": [item["task_id"] for item in tasks if item["split"] == split],
            "repetitions_per_task": 3,
            "arm_order": {"method": "seeded-balanced", "seed": seed},
        }
    return {
        "schema_version": TASK_CONTRACT_SCHEMA_VERSION,
        "task_set_id": "controller-ab-confirmatory-80-v2",
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
            "promotion_access_policy": {
                "sealed": True,
                "allowed_round": "promotion",
                "forbidden_purposes": ["development", "preflight", "pilot-debug"],
            },
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
    round_name: str = "pilot",
) -> dict[str, Any]:
    if round_name not in {"pilot", "promotion"}:
        raise CorpusError("batch round must be pilot or promotion")
    report = validate_corpus(manifest, materialized_root, round_name=round_name)
    expected_fixture_count = sum(
        item["split"] == round_name for item in expand_definitions(manifest)
    )
    if (
        report.get("fixture_status") != "ready"
        or report.get("ready_fixture_count") != expected_fixture_count
        or report.get("materialized_round") != round_name
    ):
        raise CorpusError(
            f"batch configuration requires an isolated {round_name} fixture bundle"
        )
    definitions = expand_definitions(manifest)
    tasks: dict[str, Any] = {}
    for definition in definitions:
        if definition["split"] != round_name:
            continue
        evaluator = materialized_root / definition["evaluator_path"]
        allowed = definition["allowed_paths"] or ["__read_only_no_changes__"]
        tasks[definition["fixture_id"]] = {
            "fixture": str((materialized_root / definition["actor_path"]).resolve()),
            # The generating interpreter is the one interpreter known to exist
            # wherever this configuration is consumed in the same run. "py -3"
            # is the Windows-only launcher and raised FileNotFoundError on
            # every POSIX cell, while the bare "python" used elsewhere is a
            # Store stub on Windows: no literal name works on all three.
            "hidden_check": [
                sys.executable,
                str((evaluator / "hidden_check.py").resolve()),
                "{fixture}",
            ],
            "quality_check": [
                sys.executable,
                str((evaluator / "quality_check.py").resolve()),
                "{fixture}",
                "{message}",
            ],
            "allow_changes": allowed,
            "guard_roots": [str(evaluator.resolve())],
        }
    source_root = controller_protocol.resolve().parents[1]
    identity = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "--show-toplevel", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    lines = identity.stdout.splitlines()
    if identity.returncode != 0 or len(lines) != 2:
        raise CorpusError("confirmatory source must be a Git checkout")
    if Path(lines[0]).resolve() != source_root:
        raise CorpusError("controller protocol must belong to the source Git root")
    status = subprocess.run(
        [
            "git",
            "-C",
            str(source_root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        raise CorpusError("cannot read confirmatory source Git status")
    source_git = {
        "head": lines[1].strip().lower(),
        "status_sha256": hashlib.sha256(status.stdout.encode("utf-8")).hexdigest(),
    }
    source_git["sha256"] = hashlib.sha256(
        json.dumps(source_git, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 3,
        "round_name": round_name,
        "promotion_content_accessed": round_name == "promotion",
        "task_contract": str(task_contract.resolve()),
        "controller_protocol": str(controller_protocol.resolve()),
        "plugin_source": str(source_root),
        "source_commit": source_git["head"],
        "source_git": source_git,
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
        # Exclusions apply to the path under the root, never to the absolute
        # spelling: path.parts includes every ancestor above root, so a corpus
        # checked out under any directory named like an excluded part hashed
        # as an empty tree -- and both producer and verifier agreed on the
        # empty digest, failing open.
        relative_parts = path.relative_to(root).parts
        if not path.is_file() or any(part in IGNORED_PARTS for part in relative_parts):
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


def materialize(
    manifest: dict[str, Any],
    output_root: Path,
    *,
    round_name: str | None = None,
) -> dict[str, Any]:
    if round_name not in {None, "pilot", "promotion"}:
        raise CorpusError("materialization round must be pilot or promotion")
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
    all_definitions = expand_definitions(manifest)
    definitions = [
        item
        for item in all_definitions
        if round_name is None or item["split"] == round_name
    ]
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
    promotion_ids = {
        item["fixture_id"] for item in all_definitions if item["split"] == "promotion"
    }
    promotion_locks = [item for item in locks if item["fixture_id"] in promotion_ids]
    lock = {
        "schema_version": 3,
        "kind": "controller_ab_fixture_lock",
        "corpus_id": manifest["corpus_id"],
        "materialized_round": round_name or "all",
        "declared_fixture_count": len(all_definitions),
        "materialized_fixture_count": len(locks),
        "fixture_ids": [item["fixture_id"] for item in locks],
        "contains_run_results": False,
        "definition_seal_sha256": promotion_definition_seal(all_definitions),
        "round_content_seal_sha256": _sha256(_canonical(locks)),
        "promotion_content_seal_sha256": (
            _sha256(_canonical(promotion_locks))
            if round_name in {None, "promotion"}
            else None
        ),
        "fixtures": locks,
    }
    (resolved / "corpus-lock.json").write_text(
        json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return lock


def validate_corpus(
    manifest: dict[str, Any],
    materialized_root: Path | None = None,
    *,
    round_name: str | None = None,
) -> dict[str, Any]:
    if round_name not in {None, "pilot", "promotion"}:
        raise CorpusError("validation round must be pilot or promotion")
    all_definitions = expand_definitions(manifest)
    definitions = all_definitions
    seal = promotion_definition_seal(all_definitions)
    promotion = manifest["splits"]["promotion"]
    contract_errors: list[str] = []
    if promotion.get("definition_sealed") is not True:
        contract_errors.append("promotion definitions are not sealed")
    if promotion.get("definition_seal_sha256") != seal:
        contract_errors.append("promotion definition seal does not match")
    if (
        manifest.get("promotion_content_sealed") is not True
        or manifest.get("promotion_materialized_lock_required") is not True
        or promotion.get("allowed_round") != "promotion"
        or set(promotion.get("forbidden_purposes", []))
        != {"development", "preflight", "pilot-debug"}
    ):
        contract_errors.append("promotion access and sealing policy is incomplete")
    cell_counts: dict[str, int] = {}
    for item in all_definitions:
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
    materialized_round: str | None = None
    if root is not None:
        lock_path = root / "corpus-lock.json"
        if not lock_path.is_file():
            contract_errors.append("materialized corpus lock is missing")
        else:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            if lock.get("schema_version") != 3:
                contract_errors.append(
                    "materialized corpus lock must use schema_version 3"
                )
            materialized_round = lock.get("materialized_round")
            if materialized_round not in {"all", "pilot", "promotion"}:
                contract_errors.append("materialized round is missing or invalid")
            elif round_name is not None and materialized_round != round_name:
                contract_errors.append(
                    "materialized round does not match requested round"
                )
            effective_round = round_name or (
                materialized_round
                if materialized_round in {"pilot", "promotion"}
                else None
            )
            if effective_round is not None:
                definitions = [
                    item for item in all_definitions if item["split"] == effective_round
                ]
            if lock.get("contains_run_results") is not False:
                contract_errors.append("corpus lock cannot contain run results")
            if lock.get("definition_seal_sha256") != seal:
                contract_errors.append("materialized definition seal does not match")
            lock_by_id = {
                item.get("fixture_id"): item
                for item in lock.get("fixtures", [])
                if isinstance(item, dict)
            }
            expected_ids = {item["fixture_id"] for item in definitions}
            if set(lock_by_id) != expected_ids:
                contract_errors.append(
                    "materialized fixture IDs do not exactly match round"
                )
            if lock.get("fixture_ids") != [item["fixture_id"] for item in definitions]:
                contract_errors.append(
                    "materialized fixture order does not match round"
                )
            if lock.get("declared_fixture_count") != len(all_definitions):
                contract_errors.append("declared fixture count does not match corpus")
            if lock.get("materialized_fixture_count") != len(definitions):
                contract_errors.append(
                    "materialized fixture count does not match round"
                )
            selected_locks = [
                lock_by_id[item["fixture_id"]]
                for item in definitions
                if item["fixture_id"] in lock_by_id
            ]
            if lock.get("round_content_seal_sha256") != _sha256(
                _canonical(selected_locks)
            ):
                contract_errors.append("round content seal does not match")
            promotion_locks = [
                lock_by_id[item["fixture_id"]]
                for item in all_definitions
                if item["split"] == "promotion" and item["fixture_id"] in lock_by_id
            ]
            expected_promotion_seal = (
                _sha256(_canonical(promotion_locks))
                if materialized_round in {"all", "promotion"}
                else None
            )
            if lock.get("promotion_content_seal_sha256") != expected_promotion_seal:
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
                    forbidden = ACTOR_FORBIDDEN_FIELDS.intersection(actual_payload)
                    if forbidden:
                        reasons.append(
                            "actor task payload leaks evaluator metadata: "
                            + ", ".join(sorted(forbidden))
                        )
                    try:
                        actor_payload(definition)
                    except CorpusError as error:
                        reasons.append(str(error))
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
        "schema_version": 2,
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
        "fixture_status": (
            "ready"
            if ready == len(definitions) and root is not None and not contract_errors
            else "pending"
        ),
        "materialized_round": materialized_round,
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
    validate_parser.add_argument("--round", choices=("pilot", "promotion"))
    materialize_parser = subparsers.add_parser("materialize")
    materialize_parser.add_argument("--output-root", type=Path, required=True)
    materialize_parser.add_argument(
        "--round", choices=("pilot", "promotion"), required=True
    )
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
    batch_parser.add_argument(
        "--round",
        choices=("pilot", "promotion"),
        default="pilot",
        help="expose only the selected round; promotion is never included in pilot",
    )
    batch_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = load_manifest(args.manifest)
        if args.command == "materialize":
            output = materialize(manifest, args.output_root, round_name=args.round)
        elif args.command == "validate":
            if args.materialized_root is not None and args.round is None:
                raise CorpusError("validation of a materialized root requires --round")
            output = validate_corpus(
                manifest, args.materialized_root, round_name=args.round
            )
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
                round_name=args.round,
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
