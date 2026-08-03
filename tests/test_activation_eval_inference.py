"""The part of the activation eval that decides which arm won."""

from __future__ import annotations

import math
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT / "evals") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "evals"))

from activation_core.inference import (  # noqa: E402
    EQUIVALENT,
    INFERIOR,
    NOT_PROVEN,
    SUPERIOR,
    UNDECIDABLE,
    compare_all,
    compare_paired,
    mcnemar_exact,
    paired_difference,
    paired_outcomes,
    trial_key,
    wilson_interval,
)
from activation_core.scoring import Observation  # noqa: E402
from tests.activation_eval_support import TempTreeTestCase  # noqa: E402


def observation(case_id: str, arm: str, repetition: int, passed: bool | None):
    return Observation(
        case_id=case_id,
        arm=arm,
        repetition=repetition,
        polarity="should-fire",
        lang="en",
        expect=("diagnose-systematically",),
        fired=("diagnose-systematically",) if passed else (),
        complete=passed is not None,
        incomplete_reason=None if passed is not None else "claude exited 1",
        passed=passed,
        worked_after_firing=False,
        turns=1,
        cost_usd=0.01,
        duration_seconds=1.0,
        stopped_early=False,
    )


class WilsonTests(TempTreeTestCase):
    def test_an_empty_cohort_has_no_interval_rather_than_a_zero(self) -> None:
        self.assertIsNone(wilson_interval(0, 0))

    def test_bounds_stay_inside_zero_and_one_at_the_extremes(self) -> None:
        # The reason for Wilson over the textbook interval: at 0 of 10 the
        # normal interval reaches below zero, which is not a rate.
        low_end = wilson_interval(0, 10)
        assert low_end is not None
        self.assertEqual(low_end.point, 0.0)
        self.assertGreaterEqual(low_end.low, 0.0)
        self.assertGreater(low_end.high, 0.0)
        high_end = wilson_interval(10, 10)
        assert high_end is not None
        self.assertLessEqual(high_end.high, 1.0)
        self.assertLess(high_end.low, 1.0)

    def test_more_evidence_narrows_the_interval(self) -> None:
        small = wilson_interval(5, 10)
        large = wilson_interval(50, 100)
        assert small is not None and large is not None
        self.assertEqual(small.point, large.point)
        self.assertLess(large.high - large.low, small.high - small.low)


class McNemarTests(TempTreeTestCase):
    def test_no_disagreement_is_no_evidence_not_proof_of_sameness(self) -> None:
        self.assertEqual(mcnemar_exact(0, 0), 1.0)

    def test_a_symmetric_split_is_maximally_unconvincing(self) -> None:
        self.assertEqual(mcnemar_exact(5, 5), 1.0)

    def test_a_one_sided_split_becomes_convincing_as_it_grows(self) -> None:
        # Ten disagreements all favouring one arm is the textbook 2 * 0.5**10.
        self.assertAlmostEqual(mcnemar_exact(10, 0), 2 * 0.5**10)
        self.assertGreater(mcnemar_exact(3, 0), mcnemar_exact(10, 0))

    def test_the_test_is_two_sided(self) -> None:
        self.assertEqual(mcnemar_exact(8, 1), mcnemar_exact(1, 8))

    def test_probabilities_never_exceed_one(self) -> None:
        for only_a in range(6):
            for only_b in range(6):
                with self.subTest(a=only_a, b=only_b):
                    self.assertLessEqual(mcnemar_exact(only_a, only_b), 1.0)

    def test_negative_counts_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            mcnemar_exact(-1, 2)


class PairedDifferenceTests(TempTreeTestCase):
    def test_no_pairs_yields_no_interval(self) -> None:
        self.assertIsNone(paired_difference(0, 0, 0))

    def test_concordant_pairs_shrink_the_interval_without_moving_it(self) -> None:
        """Cases both arms agreed on are the reason pairing is worth doing.

        They leave the estimate alone and still count as evidence, which is
        exactly the variance an unpaired comparison of the same runs throws
        away.
        """
        few = paired_difference(4, 0, 4)
        many = paired_difference(4, 0, 40)
        assert few is not None and many is not None
        self.assertGreater(few.point, many.point)
        self.assertLess(many.high - many.low, few.high - few.low)

    def test_the_interval_brackets_the_observed_difference(self) -> None:
        interval = paired_difference(6, 2, 20)
        assert interval is not None
        self.assertAlmostEqual(interval.point, 0.2)
        self.assertLess(interval.low, interval.point)
        self.assertGreater(interval.high, interval.point)


class VerdictTests(TempTreeTestCase):
    def outcomes(self, passes_a: int, passes_b: int, total: int):
        """Disjoint wins, so every disagreement points one way per arm."""
        a = {trial_key("c", i): i < passes_a for i in range(total)}
        b = {trial_key("c", i): total - i <= passes_b for i in range(total)}
        return a, b

    def test_a_clear_win_is_called_superior(self) -> None:
        a, b = self.outcomes(20, 0, 20)
        result = compare_paired("full", "none", a, b, margin=0.10)
        self.assertEqual(result.verdict, SUPERIOR)
        self.assertEqual(result.only_a, 20)
        self.assertLess(result.p_value, 0.001)

    def test_the_losing_direction_is_named_too(self) -> None:
        a, b = self.outcomes(0, 20, 20)
        result = compare_paired("full", "none", a, b, margin=0.10)
        self.assertEqual(result.verdict, INFERIOR)

    def test_identical_arms_over_enough_pairs_are_equivalent(self) -> None:
        shared = {trial_key("c", i): i % 3 != 0 for i in range(200)}
        result = compare_paired(
            "instruction", "full", shared, dict(shared), margin=0.10
        )
        self.assertEqual(result.verdict, EQUIVALENT)
        self.assertEqual(result.only_a, 0)
        self.assertEqual(result.only_b, 0)

    def test_identical_arms_over_too_few_pairs_are_not_proven(self) -> None:
        """The distinction the whole module exists for.

        Two arms that agreed on every one of four cases have not been shown to
        be equivalent -- four cases cannot exclude a twenty-point gap. Reporting
        that as "no difference" is how an underpowered run becomes a finding.
        """
        shared = {trial_key("c", i): True for i in range(4)}
        result = compare_paired(
            "instruction", "full", shared, dict(shared), margin=0.05
        )
        self.assertEqual(result.verdict, NOT_PROVEN)
        self.assertEqual(result.p_value, 1.0)

    def test_a_split_too_wide_to_read_is_not_proven(self) -> None:
        a, b = self.outcomes(3, 2, 10)
        result = compare_paired("full", "none", a, b, margin=0.05)
        self.assertEqual(result.verdict, NOT_PROVEN)

    def test_no_shared_trials_is_undecidable(self) -> None:
        result = compare_paired("full", "none", {"a#1": True}, {"b#1": True})
        self.assertEqual(result.verdict, UNDECIDABLE)
        self.assertEqual(result.pairs, 0)


class PairingIsMorePowerfulTests(TempTreeTestCase):
    def test_pairing_decides_what_independent_rates_cannot(self) -> None:
        """Why the arms run interleaved on the same cases.

        Thirty cases where the arms agree and six where one arm always wins is
        a decided result when read as pairs. Read as two independent rates over
        the same runs, the difference is six points against a spread that
        easily covers it, and the run reports nothing.
        """
        total, wins = 36, 6
        a = {trial_key("c", i): True for i in range(total)}
        b = {trial_key("c", i): i < total - wins for i in range(total)}
        paired = compare_paired("full", "none", a, b, margin=0.10)
        self.assertEqual(paired.verdict, SUPERIOR)
        self.assertLess(paired.p_value, 0.05)

        passes_a = sum(a.values())
        passes_b = sum(b.values())
        unpaired_a = wilson_interval(passes_a, total)
        unpaired_b = wilson_interval(passes_b, total)
        assert unpaired_a is not None and unpaired_b is not None
        # Treated as independent samples the intervals overlap, so the same
        # runs would have supported no conclusion at all.
        self.assertLess(unpaired_b.low, unpaired_a.low)
        self.assertGreater(unpaired_b.high, unpaired_a.low)


class ObservationPairingTests(TempTreeTestCase):
    def setUp(self) -> None:
        self.observations = [
            observation("a", "full", 1, True),
            observation("a", "instruction", 1, False),
            observation("a", "none", 1, None),
            observation("b", "full", 1, True),
            observation("b", "instruction", 1, True),
            observation("b", "none", 1, False),
        ]

    def test_incomplete_runs_are_absent_rather_than_failures(self) -> None:
        # A run the harness could not read is not a run the workflow failed.
        none_arm = paired_outcomes(self.observations, "none")
        self.assertNotIn(trial_key("a", 1), none_arm)
        self.assertIs(none_arm[trial_key("b", 1)], False)

    def test_arms_are_lined_up_by_trial_not_by_position(self) -> None:
        """A hole in one arm must not shift the other arm's cases along.

        `none` is missing case 'a', so a positional comparison would pair
        `full`'s 'a' against `none`'s 'b'.
        """
        result = compare_paired(
            "full",
            "none",
            paired_outcomes(self.observations, "full"),
            paired_outcomes(self.observations, "none"),
        )
        self.assertEqual(result.pairs, 1)
        self.assertEqual(result.only_a, 1)

    def test_compare_all_measures_every_arm_against_the_baseline(self) -> None:
        rows = compare_all(
            self.observations, ["none", "instruction", "full"], baseline="none"
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["armB"] for row in rows}, {"none"})
        self.assertEqual({row["armA"] for row in rows}, {"instruction", "full"})

    def test_without_a_baseline_every_pair_is_compared(self) -> None:
        rows = compare_all(self.observations, ["none", "instruction", "full"])
        self.assertEqual(len(rows), 3)

    def test_every_row_serializes_to_json_safe_values(self) -> None:
        for row in compare_all(self.observations, ["none", "full"], baseline="none"):
            for value in row.values():
                self.assertFalse(
                    isinstance(value, float) and math.isnan(value),
                    "a NaN would serialize as invalid JSON",
                )


if __name__ == "__main__":
    import unittest

    unittest.main()
