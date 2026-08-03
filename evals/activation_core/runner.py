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
from .inference import EQUIVALENT, INFERIOR, SUPERIOR, compare_all
from .scoring import Observation, bottom_skills, compare, observe, score_arm
from .session import Run, run_case
from .transcript import read


# Three concurrent sessions is the working default and four the ceiling. Above
# that the provider throttles more than the wall clock improves, and every
# retry spent waiting is a session held open for nothing.
DEFAULT_WORKERS = 3
MAX_WORKERS = 4
# How close two arms must be before the run may call them equivalent rather
# than merely undecided. Declared by the caller because it is a judgement
# about what difference would be worth paying for, not a statistical fact.
DEFAULT_MARGIN = 0.10


# Trials per worker between decision checks. Small enough that a decided run
# stops soon after it decides, large enough that the check is not paid for on
# every single invocation.
BLOCK_TRIALS = 4
# Verdicts that mean more runs would not change the answer.
SETTLED = frozenset({SUPERIOR, INFERIOR, EQUIVALENT})


class RunnerError(RuntimeError):
    """Raised when the harness cannot produce a measurement it would stand behind."""


def _decided(
    observations: Sequence[Observation], arms: Sequence[Arm], margin: float
) -> bool:
    """Whether every arm pair already has a verdict more runs cannot move.

    Stopping early is the difference between a comparison that gets run and one
    that gets planned: the full matrix is hours, and most of those hours are
    spent after the answer stopped changing. The stop is conservative -- one
    undecided pair keeps the whole run going -- and it can only ever fire on a
    verdict, never on a rate looking good so far.

    It answers one question: which arm activates more often. It says nothing
    about over-triggering, so the caller runs it over the should-fire matrix
    alone and pays for the negative pool whatever this returns.
    """
    if len(arms) < 2:
        return False
    rows = compare_all(
        observations,
        [arm.name for arm in arms],
        baseline=_baseline(arms),
        margin=margin,
    )
    return bool(rows) and all(row["verdict"] in SETTLED for row in rows)


def _baseline(arms: Sequence[Arm]) -> str | None:
    """The least-instrumented arm present, which every other arm is read against.

    Fixing the direction matters more than which direction it is: read the other
    way, "the index wins" and "the instruction loses" are the same number with
    opposite signs, and a reader comparing two runs would have to check which
    arms each one happened to be given. Fewest injections is the natural zero,
    and it is what the arm table's delta column already uses.
    """
    if not arms:
        return None
    return min(
        arms, key=lambda arm: (arm.expects_index + arm.expects_instruction, arm.name)
    ).name


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
    suite: str = "unlabelled",
    equivalence_margin: float = DEFAULT_MARGIN,
    stop_when_decided: bool = True,
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
    # Interleaved, not arm-major. Running every case of one arm before the next
    # arm begins makes wall-clock position a property of the arm: over a matrix
    # that takes hours, provider load, throttling, or a model rolled forward
    # mid-run all land on whichever arm was running at the time, and the report
    # attributes them to the injected context. The three runs of one trial now
    # sit adjacent, which is also what makes them a pair worth comparing.
    work = [
        (arm, case, repetition)
        for case in cases
        for repetition in range(1, repetitions + 1)
        for arm in arms
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

    # The stop rule and the negative pool answer different questions, so the
    # matrix is split by polarity before either runs. Left as one list, the
    # stop fired on a decided should-fire comparison and skipped every
    # should-not-fire trial after it -- 40 planned invocations in the run that
    # exposed this -- and both arms then reported an activation rate with
    # `falsePositiveRate: null` beside it. That is the one pairing this corpus
    # exists to keep together, and `cases.select` was already careful never to
    # break it, so a stop that broke it anyway read as a run that measured
    # over-triggering and found none.
    fire_work = [index for index, (_, case, _) in enumerate(work) if case.should_fire]
    negative_work = [
        index for index, (_, case, _) in enumerate(work) if not case.should_fire
    ]

    stopped_at: int | None = None
    try:
        # Results are placed by index, never appended, so the report is
        # identical whatever order the pool finishes in. A matrix whose row
        # order depended on scheduling could not be diffed against the last one.
        #
        # Submitted in blocks rather than all at once so the run can stop once
        # the answer stops changing. The arms are interleaved, so a block holds
        # whole trials and the comparison after it is over complete pairs.
        block = max(workers, len(arms)) * BLOCK_TRIALS
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for start in range(0, len(fire_work), block):
                list(pool.map(_one, fire_work[start : start + block]))
                if not stop_when_decided or start + block >= len(fire_work):
                    continue
                done_so_far = [entry[0] for entry in results if entry is not None]
                if _decided(done_so_far, arms, equivalence_margin):
                    stopped_at = start + block
                    if progress is not None:
                        progress(
                            f"stopping the comparison at {stopped_at} of "
                            f"{len(fire_work)} should-fire invocations: every arm "
                            f"pair has a verdict, and more runs cannot change it"
                        )
                    break
            # Unconditional, and after the stop rather than before it, because
            # the cheap outcomes should stay cheap on the side the stop governs
            # while the rate that has to accompany them is still paid for.
            list(pool.map(_one, negative_work))
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

    # A subtraction between two rates is not a finding. The paired comparison
    # is what lets the report say which arm won, or say the run did not decide,
    # instead of leaving a reader to eyeball a five-point gap.
    comparisons = compare_all(
        observations,
        [arm.name for arm in arms],
        baseline=_baseline(arms),
        margin=equivalence_margin,
    )

    return {
        "schemaVersion": 1,
        "kind": "cognitive-powers-activation-eval",
        "run": {
            # The report has always had a line for this and nothing ever wrote
            # it, so every Markdown run printed "Suite: None" -- including the
            # text the CI job pastes into its summary.
            "suite": suite,
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
            "stopWhenDecided": stop_when_decided,
            # Counted in should-fire invocations, which is the only cohort the
            # stop can shorten. The negative pool is absent from it because it
            # runs whether the stop fired or not.
            "stoppedAt": stopped_at,
            "shouldFireInvocations": len(fire_work),
            "plannedInvocations": len(work),
            "rateLimitedRuns": sum(
                1 for entry in results if entry and entry[1].rate_limited
            ),
        },
        "plan": plan(arms, cases, repetitions),
        "arms": scored,
        "comparisons": comparisons,
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

    The same rule now covers the ceiling, which it did not: a missing
    false-positive rate used to skip the comparison and leave the arm passing,
    so a run that never measured over-triggering was indistinguishable from one
    that measured it and found none. The two thresholds are one contract and a
    threshold only half of an arm was held to is not a threshold.
    """
    planned_negatives = payload.get("run", {}).get("shouldNotFireCases", 0)
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
        if false_positive is None:
            if planned_negatives:
                failures.append(
                    f"arm {name}: {planned_negatives} should-not-fire cases were "
                    "planned and none completed, so no false-positive rate"
                )
        elif false_positive > max_false_positive:
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
