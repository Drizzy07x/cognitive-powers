# Activation baseline v1

The first activation rate this plugin has ever had, and the answer to the
question `docs/analysis/eval-harness-mission.md` left open at 2.7: whether the
743-token `SessionStart` catalogue earns its cost over the standing instruction
alone.

**It does not.** Over 61 paired trials the two renderings are equivalent within
the declared 0.10 margin, and identical on the 59 single-workflow cases neither
run truncated. The catalogue has stopped shipping.

Everything below is one measurement on one model at one repetition. Read the
denominators; they are printed beside every rate for the reason
`evals/README.md` gives — a rate over three complete runs and a rate over thirty
look the same once the denominator is dropped.

## What was run

Two runs on 2026-08-03, both against `sonnet` through the real `claude` CLI on
Windows 11, three workers, `--max-cost-usd 0.75`, `--timeout-seconds 300`.

| | Run A — the comparison | Run B — the negative pool |
|---|---|---|
| Command | `-Full -Arm instruction,full -Reps 1 -EquivalenceMargin 0.10` | the same, plus `-Skills solve-efficiently -NoStopWhenDecided` |
| Planned | 206 invocations | 54 invocations |
| Ran | **132** — stopped on a decided verdict | 54 |
| Cases | 103 (83 should-fire, 20 should-not-fire) | 27 (7 should-fire, 20 should-not-fire) |

Run B exists because run A did not measure over-triggering at all, which is a
harness defect and is written up under [What the run exposed](#what-the-run-exposed)
below. Together they are 186 of the 206 invocations authorised.

No `none` arm was run. Both reports therefore carry `activationDelta: null`, and
**this baseline says nothing about the plugin against no injection at all** —
that comparison is still unmeasured.

## The 2.7 verdict

| A vs B | Difference (95%) | p | Pairs | A only | B only | Verdict |
|---|---|---|---|---|---|---|
| `full` vs `instruction` | **-1.6 pts [-9.2, +6.0]** | 1.0 | 61 | 2 | 3 | **equivalent** at margin 0.10 |

54 of the 61 pairs passed under both arms and 2 failed under both. Five trials
disagreed, three of them in the instruction arm's favour. Exact McNemar puts
p at 1.0, which is the honest reading of five discordant pairs splitting 3–2:
no evidence of a difference, which is not evidence of no difference — the
equivalence claim rests on the interval, not on the p-value.

The pre-registered rule in the issue and in the mission document was: if the two
arms are equivalent within the declared margin, the session-start index comes
out. The condition fired, and it fired on the arm that costs 743 tokens fewer.

### What that changed, and one deviation from the letter of the rule

`hooks/skill_activation.py` now injects `standing_message()` by default. The
catalogue rendering, `index_message()`, **stays in the file**, off unless
`COGNITIVE_POWERS_ENABLE_ACTIVATION_INDEX` is set, and the eval harness's `full`
arm is that toggle.

The rule as written said the hook should keep *only* `standing_message()`.
Deleting `index_message()` would also delete `arms.ARMS["full"]`, because that
arm is defined as the state this hook produces — and a decision whose
disconfirming arm no longer exists cannot be re-run against the next model, the
next host, or a grown corpus. The 743 tokens stop shipping either way, which is
what the rule is for; what the deviation buys is that the paragraph above stays
falsifiable. It is recorded here rather than left as a silent liberty.

`tests/test_skill_activation.py` now asserts which rendering ships. Nothing
asserted that before, which is how the catalogue came to ship for two releases
on the strength of nothing having argued against it.

## The rates

### Should-fire, whole corpus

| Arm | Activation | Passed / complete | Incomplete | 95% interval |
|---|---|---|---|---|
| `instruction` (shipped) | **91.9%** | 57 / 62 | 4 | [0.825, 0.965] |
| `full` | **87.5%** | 56 / 64 | 2 | [0.772, 0.935] |

Restricted to the 59 single-workflow trials neither run truncated, the two arms
are not merely equivalent but identical: **56 / 59 = 94.9%** each,
[0.861, 0.983].

Spanish cases are 15 / 16 = **93.8%** under both arms. The one miss is the same
case in both, so it is a property of the case rather than of the arm.

### Should-not-fire

| Arm | False positives | Fired / complete | Incomplete | 95% interval |
|---|---|---|---|---|
| `instruction` (shipped) | **5.3%** | 1 / 19 | 1 | [0.009, 0.246] |
| `full` | **0.0%** | 0 / 20 | 0 | [0.000, 0.161] |

The single false positive is `nearmiss-is-data-an-ok-name`, which drew
`design-review`. The direction here mildly favours the catalogue — 0 against 1 —
and on 19 and 20 runs it is one event, well inside both intervals. It is
recorded rather than dropped because it is the only evidence that points the
other way, and a baseline that printed only the evidence agreeing with its
verdict would not be one.

### Per workflow, shipped arm, whole corpus

Fifteen of the nineteen workflows had should-fire cases reached before the stop.
Twelve are 4 / 4. Three are 3 / 4: `audit-capabilities`,
`explore-web-adaptively` and `refactor-cleanly`. `solve-efficiently` is 3 / 3.
The four workflows the stop never reached have **no rate at all** in this
baseline rather than a zero.

Two failures are worth naming individually.

**`refactor-cleanly-coupon-rounding-copies` fired `legacy-safe-changes` under
both arms.** A misroute that reproduces under every configuration tried is not
noise, and it is the pair 1.7.4 already had to separate once — the vocabulary of
duplicated logic that has diverged sits in both listings.

**Four cases drew nothing at all**: `audit-capabilities-eight-things-in-folder`
and `explore-web-adaptively-buried-report` under `instruction`,
`eli5-not-a-mathematician` and `execute-durably-context-loss-refactor` under
`full`. None reproduced across arms, so at one repetition each is a coin flip
rather than a finding.

## The multi-workflow cohort is confounded, and is not reported as a rate

Run A scored the seven multi-workflow cases at 1 / 3 for `instruction` and
0 / 5 for `full`. That number should not be read as a plugin defect, and the
reason is in the observations rather than in the report.

**All six runs run A discarded as `claude exited 1` are multi-workflow runs
whose recorded `cost_usd` is at or above the `--max-cost-usd 0.75` ceiling** —
0.757, 0.768, 0.782, 0.815, and two at 1.13. The per-invocation budget is
terminating precisely the longest runs, which are precisely the ones that would
have to reach a second workflow, and `_stream_error` files that termination
under the same reason as a genuine crash. The surviving multi-workflow runs
completed at 0.54 to 0.74, against the same ceiling.

Run B, over three of the same cases, scored 2 / 3 and 1 / 2. Same cases, same
model, one repetition apart, opposite direction. Whatever the multi-workflow
rate is, this baseline has not measured it.

Two things would have to change before it could be: the budget termination needs
its own incompleteness reason so it stops being indistinguishable from a crash,
and the cohort needs a ceiling high enough that a two-workflow session is not cut
off mid-answer.

## Thresholds

`.github/workflows/activation-eval.yml` carried a floor of 0.70 and a
false-positive ceiling of 0.15 as declared defaults. Both now have a
measurement behind them.

**Floor: 0.70 → 0.75.** The shipped arm's lower bound over the whole corpus is
0.825. What the job actually gates is the quick suite, and that cohort was
15 / 15 here — a lower bound of 0.796 on fifteen runs. 0.75 clears both; 0.80
would sit above the quick cohort's bound and fail a healthy configuration on
sampling noise.

**Ceiling: 0.15, left where it is, deliberately.** The shipped arm measured
0.053, but nineteen runs put the upper bound at 0.246, so 0.15 is already
tighter than the measurement can defend and there is nothing to tighten it
with. On the quick suite's eight negative cases at three repetitions it trips on
the fourth false positive out of twenty-four — roughly a 4% chance of failing a
healthy configuration, which is the price of catching a real one.

**The job's pull-request path was measuring `full`.** It now measures
`instruction`, because that is what ships. A gate pointed at a configuration no
user has is a gate that can go green while the shipped one rots.

## What the run exposed

Two defects in the harness, both fixed with tests that fail against the previous
code.

**The early stop skipped the entire should-not-fire pool.** Run A settled its
verdict after 132 of 206 invocations, and every one of the twenty should-not-fire
cases sat after the stop. Both arms reported an activation rate with
`falsePositiveRate: null` beside it. `evals/README.md` states that the negative
pool always runs so that an activation rate is never reported without its
false-positive rate, and `cases.select` is written carefully to preserve exactly
that — but the stop rule, which lands first, was defined over the should-fire
comparison alone and did not know the promise existed. The matrix is now split by
polarity: the stop shortens the should-fire side, and the negative pool runs
whether it fired or not.

**`runner.gate` passed an arm whose false-positive rate did not exist.** It
already refused to pass an arm with no activation rate, on the stated ground
that treating an unmeasurable arm as meeting its floor is how a broken harness
reports a healthy plugin. The same reasoning was not applied to the ceiling, so
a run that never measured over-triggering was indistinguishable from one that
measured it and found none — and combined with the defect above, a dispatch run
of the CI job with three arms could stop early and report green against a
ceiling nothing had been compared to. The pull-request path was spared only by
accident: it runs one arm, and `_decided` returns `False` below two.

## What this baseline still does not say

1. **Nothing about Codex.** The harness spawns `claude`; activation is detected
   as a `Skill` tool use, which Codex does not have. The catalogue was retired
   on both hosts because it had never had evidence on either, and the one host
   that could be measured found it bought nothing — but that is an argument from
   the absence of evidence, which is the same argument that put it there.
2. **Nothing about any model but `sonnet`.**
3. **Nothing about repetition.** One repetition per case, so every per-case
   outcome is a single draw and `flippedCases` is empty by construction rather
   than by stability.
4. **Nothing about the multi-workflow cohort**, per the section above.
5. **Nothing about outcome.** A configuration that fires the right workflow and
   produces worse work would score perfectly here. That is a different mission.

## Raw artefacts

`evals/artifacts/` is git-ignored, so the JSON and Markdown from both runs live
only on the machine that ran them. The numbers above are transcribed from
`baseline-v1.json` and `baseline-v1-negatives.json`; re-running the two commands
in [What was run](#what-was-run) reproduces them, subject to the sampling this
document is careful to keep visible.

Measured spend is partial by construction: 62 of the 186 invocations emitted a
terminal result event carrying `total_cost_usd`, summing to **$25.88**. The
other 124 are should-fire runs killed on the line carrying their `Skill`
invocation, which is exactly why they are cheap and exactly why the harness
cannot price them.
