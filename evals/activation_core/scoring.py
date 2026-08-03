#!/usr/bin/env python3
"""Turn observations into rates, and refuse to invent the ones it cannot.

The discipline this module exists to enforce: a rate is a property of complete
observations only. A cohort with no complete run has no rate at all, and says
so, rather than serializing 0.0 and being read as a workflow that never fires.
An eval whose own failures are indistinguishable from the failures it measures
is worse than no eval, because it produces numbers.
"""

from __future__ import annotations

from typing import Any, Iterable, NamedTuple, Sequence

from .cases import Case
from .transcript import MISSING_TERMINAL, Reading, arm_mismatch

COMPLETE = "complete"
PARTIAL = "partial"
EMPTY = "empty"


class Observation(NamedTuple):
    """One repetition of one case under one arm, after judgement."""

    case_id: str
    arm: str
    repetition: int
    polarity: str
    lang: str
    expect: tuple[str, ...]
    fired: tuple[str, ...]
    complete: bool
    incomplete_reason: str | None
    passed: bool | None
    worked_after_firing: bool
    turns: int
    cost_usd: float | None
    duration_seconds: float
    stopped_early: bool

    @property
    def any_fired(self) -> bool:
        return bool(self.fired)

    @property
    def misrouted(self) -> bool:
        """Fired, but not what the case asked for. Only meaningful when complete."""
        return bool(self.complete and self.expect and self.fired and not self.passed)


def observe(
    case: Case,
    arm_name: str,
    repetition: int,
    reading: Reading,
    *,
    expects_index: bool,
    expects_instruction: bool,
    duration_seconds: float,
    stopped_early: bool,
    stream_error: str | None = None,
) -> Observation:
    """Judge one run, demoting it to incomplete for any reason it cannot count.

    Three independent things can spoil a run: the stream, the arm, and the
    harness's own early stop. Only the first is the host's fault, and all three
    have to demote rather than score, so they are collected in one place where
    a new reason cannot be added without passing through the same demotion.
    """
    # Early stopping is legitimate only where it cannot change the verdict:
    # the runner stops a should-fire case once the expectation is met, and
    # never stops a should-not-fire case. A stop recorded anywhere else means
    # the runner and the scorer disagree about what settles a case.
    settled_early = stopped_early and case.should_fire
    reason = stream_error or reading.incomplete_reason
    # A deliberately stopped run has no terminal event because nothing was left
    # to wait for. Scoring that as a truncated stream threw away every run in
    # which the workflow fired -- the whole positive half of the measurement.
    if settled_early and reason == MISSING_TERMINAL:
        reason = None
    if reason is None:
        reason = arm_mismatch(reading, expects_index, expects_instruction)
    if reason is None and stopped_early and not case.should_fire:
        reason = "run was stopped early on a case that can only pass by finishing"

    complete = reason is None
    return Observation(
        case_id=case.case_id,
        arm=arm_name,
        repetition=repetition,
        polarity=case.polarity,
        lang=case.lang,
        expect=case.expect,
        fired=reading.fired,
        complete=complete,
        incomplete_reason=reason,
        passed=case.satisfied_by(reading.fired) if complete else None,
        worked_after_firing=reading.worked_after_firing,
        turns=reading.turns,
        cost_usd=reading.cost_usd,
        duration_seconds=duration_seconds,
        stopped_early=stopped_early,
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def summarize(observations: Sequence[Observation]) -> dict[str, Any]:
    """Summarize a cohort, omitting every rate the cohort cannot support."""
    total = len(observations)
    if total == 0:
        return {"status": EMPTY, "total": 0, "complete": 0, "incomplete": 0}

    complete = [item for item in observations if item.complete]
    incomplete = total - len(complete)
    summary: dict[str, Any] = {
        "status": COMPLETE
        if complete and not incomplete
        else (PARTIAL if complete else EMPTY),
        "total": total,
        "complete": len(complete),
        "incomplete": incomplete,
    }
    if not complete:
        summary["reasons"] = _reason_counts(observations)
        return summary

    passed = sum(1 for item in complete if item.passed)
    fired = sum(1 for item in complete if item.any_fired)
    misrouted = sum(1 for item in complete if item.misrouted)
    summary.update(
        {
            "passed": passed,
            "failed": len(complete) - passed,
            "passRate": _rate(passed, len(complete)),
            "firedRuns": fired,
            "firedRate": _rate(fired, len(complete)),
            "misroutedRuns": misrouted,
        }
    )
    if incomplete:
        summary["reasons"] = _reason_counts(observations)
    return summary


def _reason_counts(observations: Iterable[Observation]) -> dict[str, int]:
    """Count why runs were excluded, rather than listing the distinct reasons.

    The distinct set answers "what went wrong"; the counts answer "how much of
    this arm is missing", which is the question that decides whether the arm's
    rate is comparable to the one beside it.
    """
    counts: dict[str, int] = {}
    for item in observations:
        if item.complete:
            continue
        reason = item.incomplete_reason or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda entry: (-entry[1], entry[0])))


def case_stability(observations: Sequence[Observation]) -> dict[str, Any]:
    """Per-case behaviour across repetitions, including whether it flipped.

    Activation is not deterministic, so a case that passed twice of three is a
    different fact from one that passed three of three, and reporting only the
    mean hides which. ``flipped`` is the one a reader should act on: it marks a
    case whose outcome is not yet a property of the configuration.
    """
    complete = [item for item in observations if item.complete]
    passed = sum(1 for item in complete if item.passed)
    runs = len(complete)
    rate = _rate(passed, runs)
    return {
        "runs": len(observations),
        "complete": runs,
        "passed": passed,
        "passRate": rate,
        # Population variance of the Bernoulli outcomes: p(1-p). Zero for a
        # case that always agreed with itself, maximal at an even split.
        "variance": round(rate * (1 - rate), 4) if rate is not None else None,
        "flipped": bool(runs > 1 and 0 < passed < runs),
    }


def _group(observations: Iterable[Observation], key) -> dict[Any, list[Observation]]:
    buckets: dict[Any, list[Observation]] = {}
    for item in observations:
        buckets.setdefault(key(item), []).append(item)
    return buckets


def score_arm(
    arm_name: str,
    observations: Sequence[Observation],
    cases: Sequence[Case],
) -> dict[str, Any]:
    """The whole picture for one arm: overall, per polarity, per skill, per case."""
    by_id = {case.case_id: case for case in cases}
    positives = [item for item in observations if item.polarity == "should-fire"]
    negatives = [item for item in observations if item.polarity == "should-not-fire"]

    per_skill: dict[str, dict[str, Any]] = {}
    for name, group in sorted(
        _group(
            (item for item in positives if len(item.expect) == 1),
            lambda item: item.expect[0],
        ).items()
    ):
        per_skill[name] = summarize(group)

    per_case: dict[str, dict[str, Any]] = {}
    for case_id, group in sorted(
        _group(observations, lambda item: item.case_id).items()
    ):
        entry = case_stability(group)
        case = by_id.get(case_id)
        if case is not None:
            entry["polarity"] = case.polarity
            entry["expect"] = list(case.expect)
            entry["lang"] = case.lang
        entry["firedNames"] = sorted({name for item in group for name in item.fired})
        per_case[case_id] = entry

    negative_complete = [item for item in negatives if item.complete]
    false_positives = sum(1 for item in negative_complete if item.any_fired)

    return {
        "arm": arm_name,
        "overall": summarize(observations),
        "shouldFire": summarize(positives),
        "shouldNotFire": summarize(negatives),
        "falsePositiveRate": _rate(false_positives, len(negative_complete)),
        "multiSkill": summarize([item for item in positives if len(item.expect) > 1]),
        "spanish": summarize([item for item in observations if item.lang == "es"]),
        "perSkill": per_skill,
        "perCase": per_case,
        "flippedCases": sorted(
            case_id for case_id, entry in per_case.items() if entry["flipped"]
        ),
        "workedAfterFiring": sum(
            1 for item in observations if item.complete and item.worked_after_firing
        ),
        # A lower bound, and labelled as one. Cost is reported by the terminal
        # event, which a deliberately stopped run never reaches, so the runs
        # that were cheapest to stop are exactly the ones missing from the sum.
        "cost": {
            "runs": len(observations),
            "observedUsdLowerBound": round(
                sum(item.cost_usd or 0.0 for item in observations), 4
            ),
            "runsWithoutReportedCost": sum(
                1 for item in observations if item.cost_usd is None
            ),
            "stoppedEarly": sum(1 for item in observations if item.stopped_early),
        },
    }


def bottom_skills(scored: dict[str, Any], count: int = 5) -> list[dict[str, Any]]:
    """The worst-performing workflows, skipping any with no complete run.

    A workflow whose every run failed to complete is not a workflow that ranks
    last; it is a workflow that was not measured, and putting it at the bottom
    of a ranking would read as the opposite of what the data says.
    """
    ranked = [
        {"skill": name, "passRate": entry["passRate"], "complete": entry["complete"]}
        for name, entry in scored.get("perSkill", {}).items()
        if entry.get("passRate") is not None
    ]
    ranked.sort(key=lambda entry: (entry["passRate"], entry["skill"]))
    return ranked[:count]


def compare(arms: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Deltas between arms on the two rates the mission turns on."""
    rows: list[dict[str, Any]] = []
    for scored in arms:
        should_fire = scored["shouldFire"]
        rows.append(
            {
                "arm": scored["arm"],
                "activationRate": should_fire.get("passRate"),
                "falsePositiveRate": scored.get("falsePositiveRate"),
                "complete": should_fire.get("complete", 0),
                "incomplete": should_fire.get("incomplete", 0),
            }
        )
    baseline = next((row for row in rows if row["arm"] == "none"), None)
    for row in rows:
        if baseline is None or row["activationRate"] is None:
            row["activationDelta"] = None
            continue
        if baseline["activationRate"] is None:
            row["activationDelta"] = None
            continue
        row["activationDelta"] = round(
            row["activationRate"] - baseline["activationRate"], 4
        )
    return {"arms": rows}
