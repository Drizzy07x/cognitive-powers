#!/usr/bin/env python3
"""Drive the corpus across arms and repetitions, and gate on the result.

Two things happen before any process is spawned: the corpus is validated in
full, and the planned work is written out. The first is a money guard -- a typo
in case ninety should not cost eighty-nine runs to discover. The second is the
honesty guard: the plan enumerates every intended (arm, case, repetition), so a
run that never happened is a gap in the results rather than an absence nobody
can see.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Sequence

from . import fixtures
from .arms import Arm, arm as get_arm
from .cases import Case
from .scoring import Observation, bottom_skills, compare, observe, score_arm
from .session import Run, run_case
from .transcript import read


# Three concurrent sessions is the working default and four the ceiling. Above
# that the provider throttles more than the wall clock improves, and every
# retry spent waiting is a session held open for nothing.
DEFAULT_WORKERS = 3
MAX_WORKERS = 4


class RunnerError(RuntimeError):
    """Raised when the harness cannot produce a measurement it would stand behind."""


def plan(
    arms: Sequence[Arm], cases: Sequence[Case], repetitions: int
) -> list[dict[str, Any]]:
    """Every intended invocation, written before any of them runs."""
    return [
        {
            "arm": arm.name,
            "case": case.case_id,
            "repetition": repetition,
            "polarity": case.polarity,
            "expect": list(case.expect),
        }
        for arm in arms
        for case in cases
        for repetition in range(1, repetitions + 1)
    ]


def _stream_error(run: Run) -> str | None:
    """Name a failure of the invocation itself, before the stream is read."""
    if run.timed_out:
        return "run exceeded the wall-clock timeout"
    if run.exit_code != 0 and not run.stopped_early:
        tail = " ".join(run.stderr.split())[-160:]
        return (
            f"claude exited {run.exit_code}: {tail}"
            if tail
            else (f"claude exited {run.exit_code}")
        )
    return None


def execute(
    *,
    cases: Sequence[Case],
    arms: Sequence[Arm],
    repetitions: int,
    plugin_root: Path,
    installed: frozenset[str],
    python_executable: str,
    model: str,
    max_cost_usd: float,
    timeout_seconds: float,
    claude_executable: str,
    artifacts: Path | None,
    workspace_root: Path | None = None,
    progress: Callable[[str], None] | None = None,
    runner: Callable[..., Run] = run_case,
    workers: int = DEFAULT_WORKERS,
) -> dict[str, Any]:
    """Run the whole matrix and return the scored payload."""
    if not cases:
        raise RunnerError("the selection matched no cases")
    if repetitions < 1:
        raise RunnerError("repetitions must be at least 1")
    if not 1 <= workers <= MAX_WORKERS:
        raise RunnerError(f"workers must be between 1 and {MAX_WORKERS}")

    unknown = sorted({case.fixture for case in cases} - set(fixtures.fixture_names()))
    if unknown:
        raise RunnerError(f"corpus names unknown fixtures: {', '.join(unknown)}")

    owned = workspace_root is None
    base = (
        Path(workspace_root)
        if workspace_root
        else Path(tempfile.mkdtemp(prefix="cp-activation-"))
    )
    work = [
        (arm, case, repetition)
        for arm in arms
        for case in cases
        for repetition in range(1, repetitions + 1)
    ]
    results: list[tuple[Observation, Run] | None] = [None] * len(work)
    done = threading.Lock()
    finished = [0]

    def _one(index: int) -> None:
        arm, case, repetition = work[index]
        try:
            run = runner(
                case,
                arm,
                repetition,
                plugin_root=plugin_root,
                workspace_root=base / arm.name,
                python_executable=python_executable,
                installed=installed,
                model=model,
                max_cost_usd=max_cost_usd,
                timeout_seconds=timeout_seconds,
                claude_executable=claude_executable,
            )
        except Exception as error:  # noqa: BLE001 - one case must not end a matrix
            # A run that could not be spawned is one missing observation, not a
            # reason to discard the two hundred already paid for. It becomes an
            # incomplete observation carrying the failure, which keeps it out of
            # every denominator while leaving the gap visible in the report.
            run = Run(
                case_id=case.case_id,
                arm=arm.name,
                repetition=repetition,
                stream="",
                stderr=f"{type(error).__name__}: {error}",
                exit_code=-1,
                duration_seconds=0.0,
                stopped_early=False,
                timed_out=False,
            )
        reading = read(run.stream, installed)
        observation = observe(
            case,
            arm.name,
            repetition,
            reading,
            expects_index=arm.expects_index,
            expects_instruction=arm.expects_instruction,
            duration_seconds=run.duration_seconds,
            stopped_early=run.stopped_early,
            stream_error=_stream_error(run),
        )
        results[index] = (observation, run)
        if progress is not None:
            # Reported on completion rather than on start, so the line is a
            # fact about a run that happened. With several workers in flight,
            # a start line would interleave into an order no result follows.
            with done:
                finished[0] += 1
                progress(
                    f"[{finished[0]}/{len(work)}] {arm.name}/{case.case_id}"
                    f"#{repetition} -> {'fired ' + ','.join(observation.fired) if observation.fired else 'silent'}"
                )

    try:
        # Results are placed by index, never appended, so the report is
        # identical whatever order the pool finishes in. A matrix whose row
        # order depended on scheduling could not be diffed against the last one.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(_one, range(len(work))))
    finally:
        if owned:
            # Workspaces hold nothing worth keeping and everything worth not
            # leaving behind: a settings file naming the operator's interpreter
            # and a durable-state root written by the plugin under test.
            shutil.rmtree(base, ignore_errors=True)

    observations = [entry[0] for entry in results if entry is not None]
    transcripts = (
        [_write_transcript(artifacts, entry[1]) for entry in results if entry]
        if artifacts is not None
        else []
    )

    scored = [
        score_arm(
            arm.name, [item for item in observations if item.arm == arm.name], cases
        )
        for arm in arms
    ]
    # The delta against the no-injection control is the number this experiment
    # exists for, so it is attached to the arm rather than computed only in a
    # separate comparison block the Markdown never reads. It was advertised as
    # a column before it was computed, which printed a dash on every row.
    deltas = {row["arm"]: row["activationDelta"] for row in compare(scored)["arms"]}
    for entry in scored:
        entry["activationDelta"] = deltas.get(entry["arm"])
        entry["bottomSkills"] = bottom_skills(entry)

    return {
        "schemaVersion": 1,
        "kind": "cognitive-powers-activation-eval",
        "run": {
            "model": model,
            "repetitions": repetitions,
            "cases": len(cases),
            "shouldFireCases": sum(1 for case in cases if case.should_fire),
            "shouldNotFireCases": sum(1 for case in cases if not case.should_fire),
            "arms": [arm.name for arm in arms],
            "invocations": len(observations),
            "maxCostUsd": max_cost_usd,
            "timeoutSeconds": timeout_seconds,
            "workers": workers,
            # Retries are reported rather than hidden: a matrix that needed
            # forty of them was measured against a throttled provider, and
            # that belongs beside its rates.
            "retriedRuns": sum(
                1 for entry in results if entry and entry[1].attempts > 1
            ),
            "rateLimitedRuns": sum(
                1 for entry in results if entry and entry[1].rate_limited
            ),
        },
        "plan": plan(arms, cases, repetitions),
        "arms": scored,
        "observations": [item._asdict() for item in observations],
        "transcripts": transcripts,
    }


def _write_transcript(artifacts: Path, run: Run) -> dict[str, Any]:
    """Persist one raw stream outside the repository and return its location."""
    directory = artifacts / run.arm
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run.case_id}-{run.repetition}.jsonl"
    path.write_text(run.stream, encoding="utf-8", newline="\n")
    return {
        "arm": run.arm,
        "case": run.case_id,
        "repetition": run.repetition,
        "path": str(path),
    }


def gate(
    payload: dict[str, Any], *, floor: float, max_false_positive: float
) -> list[str]:
    """Return the reasons this run fails its thresholds, or an empty list.

    An arm that produced no complete should-fire run fails rather than passing
    silently. The alternative -- treating an unmeasurable arm as meeting its
    floor -- is how a broken harness reports a healthy plugin.
    """
    failures: list[str] = []
    for scored in payload.get("arms", []):
        name = scored["arm"]
        should_fire = scored.get("shouldFire", {})
        rate = should_fire.get("passRate")
        if rate is None:
            failures.append(
                f"arm {name}: no complete should-fire run, so no activation rate"
            )
        elif rate < floor:
            failures.append(
                f"arm {name}: activation {rate:.2f} is below the floor {floor:.2f}"
            )
        false_positive = scored.get("falsePositiveRate")
        if false_positive is not None and false_positive > max_false_positive:
            failures.append(
                f"arm {name}: false positives {false_positive:.2f} exceed "
                f"{max_false_positive:.2f}"
            )
    return failures


def write_outputs(
    payload: dict[str, Any],
    *,
    json_path: Path | None,
    markdown_path: Path | None,
) -> None:
    from .report import as_json, as_markdown

    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(as_json(payload), encoding="utf-8", newline="\n")
    if markdown_path is not None:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(as_markdown(payload), encoding="utf-8", newline="\n")


def summary_line(payload: dict[str, Any]) -> str:
    parts = []
    for scored in payload.get("arms", []):
        rate = scored.get("shouldFire", {}).get("passRate")
        shown = "--" if rate is None else f"{rate * 100:.0f}%"
        parts.append(f"{scored['arm']}={shown}")
    return " ".join(parts) or "no arms scored"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = [
    "RunnerError",
    "execute",
    "gate",
    "get_arm",
    "load_json",
    "plan",
    "summary_line",
    "write_outputs",
]
