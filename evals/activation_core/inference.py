#!/usr/bin/env python3
"""Decide whether one arm actually beat another, or say the run cannot tell.

The harness could report two rates and a subtraction. That is not evidence:
activation is a coin flip run a few times per case, and a five-point gap over
eighty runs is what two identical configurations look like about a third of the
time. The mission this serves asks whether one arm "matches" another *within
measured variance*, which is a claim about an interval, and nothing here could
compute one.

Two design choices carry most of the value.

**Pairing.** Every arm answers the same case, so the arms are not independent
samples and must not be compared as though they were. Comparing paired data
pairwise removes the variance that comes from cases being easy or hard, which
is the dominant term: the same forty cases under two arms decide far more than
eighty runs split between them. The controller A/B in this repository already
works this way -- `paired-provider-backed`, paired successful runs only -- and
this module follows it rather than inventing a second standard.

**A stated margin, and a `not-proven` verdict.** "No significant difference" is
not "the same". An interval that spans both a real gain and a real loss is a
run that did not decide, and it is reported as such. Only an interval that fits
entirely inside the margin the caller declared may be called equivalent.

Standard library only, like everything else the gate runs.
"""

from __future__ import annotations

import math
from typing import Iterable, Mapping, NamedTuple, Sequence

# 1.959963985 is the two-sided 95% normal quantile. Spelled out rather than
# imported so the interval a reader sees named "95%" is the one computed.
Z_95 = 1.959963984540054

SUPERIOR = "superior"
INFERIOR = "inferior"
EQUIVALENT = "equivalent"
NOT_PROVEN = "not-proven"
UNDECIDABLE = "no-paired-observations"


class Interval(NamedTuple):
    """A point estimate with the bounds that say how much to trust it."""

    point: float
    low: float
    high: float

    def as_dict(self) -> dict[str, float]:
        return {
            "point": round(self.point, 4),
            "low": round(self.low, 4),
            "high": round(self.high, 4),
        }


def wilson_interval(successes: int, total: int, z: float = Z_95) -> Interval | None:
    """Score interval for a proportion, or None when there is nothing to bound.

    Wilson rather than the textbook normal interval because the rates here sit
    near 0 and 1 with denominators in the tens, exactly where the normal
    interval produces bounds below zero and above one and stops being readable.
    """
    if total <= 0:
        return None
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    spread = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return Interval(proportion, max(0.0, centre - spread), min(1.0, centre + spread))


def mcnemar_exact(only_a: int, only_b: int) -> float:
    """Two-sided exact p-value for a paired binary comparison.

    Only the pairs that disagree carry information: a case both arms passed and
    a case both arms failed say nothing about which is better. Under the null
    each disagreement is a fair coin, so the count of one kind is binomial and
    the exact tail needs no approximation and no dependency.

    Returns 1.0 when nothing disagreed, which is the honest reading: no
    evidence of a difference, not evidence of no difference.
    """
    if only_a < 0 or only_b < 0:
        raise ValueError("discordant counts cannot be negative")
    total = only_a + only_b
    if total == 0:
        return 1.0
    smaller = min(only_a, only_b)
    tail = sum(math.comb(total, k) for k in range(smaller + 1)) / (2**total)
    return min(1.0, 2 * tail)


def paired_difference(
    only_a: int, only_b: int, pairs: int, z: float = Z_95
) -> Interval | None:
    """Interval for the paired difference in rates, arm A minus arm B.

    The concordant pairs still belong in the denominator -- they are cases that
    were run -- but only the discordant ones move the estimate, which is what
    makes the paired interval tighter than two independent ones over the same
    work.
    """
    if pairs <= 0:
        return None
    observed = (only_a - only_b) / pairs
    # Half an observation added to each cell of the 2x2 table, after Agresti and
    # Min. Without it the interval degenerates exactly where a short run lands:
    # zero disagreements gives zero estimated variance, so four cases on which
    # two arms happened to agree came back as a zero-width interval and were
    # declared equivalent. Four cases cannot exclude a twenty-point gap, and a
    # harness whose confidence peaks when it has seen least is worse than one
    # with no interval at all.
    adjusted_pairs = pairs + 2
    adjusted_a, adjusted_b = only_a + 0.5, only_b + 0.5
    centre = (adjusted_a - adjusted_b) / adjusted_pairs
    variance = adjusted_a + adjusted_b - (adjusted_a - adjusted_b) ** 2 / adjusted_pairs
    error = math.sqrt(max(variance, 0.0)) / adjusted_pairs
    return Interval(
        observed, max(-1.0, centre - z * error), min(1.0, centre + z * error)
    )


class PairedComparison(NamedTuple):
    """Everything the harness is willing to claim about one pair of arms."""

    arm_a: str
    arm_b: str
    pairs: int
    both_passed: int
    both_failed: int
    only_a: int
    only_b: int
    difference: Interval | None
    p_value: float
    verdict: str
    margin: float

    def as_dict(self) -> dict[str, object]:
        return {
            "armA": self.arm_a,
            "armB": self.arm_b,
            "pairs": self.pairs,
            "bothPassed": self.both_passed,
            "bothFailed": self.both_failed,
            "onlyA": self.only_a,
            "onlyB": self.only_b,
            "difference": self.difference.as_dict() if self.difference else None,
            "pValue": round(self.p_value, 4),
            "verdict": self.verdict,
            "margin": self.margin,
        }


def _verdict(difference: Interval | None, margin: float, pairs: int) -> str:
    """Name what the interval supports, and refuse to name more.

    Four outcomes rather than two. An interval clear of zero is a win; an
    interval inside the margin is equivalence the caller declared in advance;
    anything spanning both is a run that did not decide, and calling that
    "no difference" is how a harness reports an underpowered run as a result.
    """
    if pairs <= 0 or difference is None:
        return UNDECIDABLE
    if difference.low > 0:
        return SUPERIOR
    if difference.high < 0:
        return INFERIOR
    if difference.low >= -margin and difference.high <= margin:
        return EQUIVALENT
    return NOT_PROVEN


def compare_paired(
    arm_a: str,
    arm_b: str,
    outcomes_a: Mapping[str, bool],
    outcomes_b: Mapping[str, bool],
    *,
    margin: float = 0.10,
    z: float = Z_95,
) -> PairedComparison:
    """Compare two arms over the trials both of them completed.

    Keyed by trial rather than positionally: a run demoted to incomplete leaves
    a hole, and lining the two arms up by order would then compare one arm's
    case against the other's neighbour.
    """
    shared = sorted(set(outcomes_a) & set(outcomes_b))
    both_passed = both_failed = only_a = only_b = 0
    for key in shared:
        passed_a, passed_b = outcomes_a[key], outcomes_b[key]
        if passed_a and passed_b:
            both_passed += 1
        elif passed_a:
            only_a += 1
        elif passed_b:
            only_b += 1
        else:
            both_failed += 1
    difference = paired_difference(only_a, only_b, len(shared), z)
    return PairedComparison(
        arm_a=arm_a,
        arm_b=arm_b,
        pairs=len(shared),
        both_passed=both_passed,
        both_failed=both_failed,
        only_a=only_a,
        only_b=only_b,
        difference=difference,
        p_value=mcnemar_exact(only_a, only_b),
        verdict=_verdict(difference, margin, len(shared)),
        margin=margin,
    )


def trial_key(case_id: str, repetition: int) -> str:
    """The identity an arm's run shares with the same run under another arm."""
    return f"{case_id}#{repetition}"


def paired_outcomes(observations: Iterable, arm: str) -> dict[str, bool]:
    """Pass/fail by trial for one arm, skipping runs that did not count.

    Incomplete runs are absent rather than false. A run the harness could not
    read is not a run the workflow failed, and folding the two together would
    let the harness's own flakiness read as a losing arm.
    """
    return {
        trial_key(item.case_id, item.repetition): bool(item.passed)
        for item in observations
        if item.arm == arm and item.complete
    }


def compare_all(
    observations: Sequence,
    arms: Sequence[str],
    *,
    baseline: str | None = None,
    margin: float = 0.10,
) -> list[dict[str, object]]:
    """Every arm against the baseline, or against each other when none is named."""
    by_arm = {arm: paired_outcomes(observations, arm) for arm in arms}
    pairs: list[tuple[str, str]] = []
    if baseline is not None and baseline in by_arm:
        pairs = [(arm, baseline) for arm in arms if arm != baseline]
    else:
        pairs = [
            (arms[i], arms[j])
            for i in range(len(arms))
            for j in range(i + 1, len(arms))
        ]
    return [
        compare_paired(a, b, by_arm[a], by_arm[b], margin=margin).as_dict()
        for a, b in pairs
    ]
