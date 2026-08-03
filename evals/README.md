# Activation eval

Measures whether Cognitive Powers workflows actually fire on requests phrased the way requests
arrive, and whether they stay quiet on requests where none should.

This is a different question from `scripts/run_skill_routing_benchmarks.py`. That benchmark scores
`skill_routing.decide` — which workflow *ranks* first for a prompt written against the skill
descriptions. It cannot see a workflow that never fires at all, because its prompts were written
from the descriptions it is scoring. This harness spawns the real host, sends a prompt written from
the workflow bodies instead, and reads what the session did.

**Nothing here runs offline.** Every case spawns `claude` and costs money, which is why it sits
outside `scripts/validate_all.py` alongside the semantic and browser runners. What the gate does
cover is the judgement path — corpus loading, transcript reading, arm verification and scoring are
pure functions with their own tests in `tests/test_activation_eval_*.py`.

## Running it

```powershell
# Check the corpus and see what a run would cost, without spawning anything.
& $python evals/run_activation_eval.py --validate-only --full

# The reduced suite against the shipped configuration.
./evals/run.ps1 -Quick

# All three arms, whole corpus, three repetitions. Roughly a thousand invocations.
# Arms are comma-separated: PowerShell binds an array parameter once, so
# repeating -Arm is an error rather than a second value.
./evals/run.ps1 -Full -Arm none,instruction,full -Reps 3 -JsonOutput C:\tmp\activation.json

# One workflow, with the should-not-fire pool that always rides along.
./evals/run.ps1 -Skills diagnose-systematically,refactor-cleanly -Reps 5
```

`run.ps1` is a wrapper; every switch maps onto the Python flag of the same name, and anything after
`--` is forwarded unchanged.

## The corpus

`cases/` holds three files. Every case declares the workflows that must fire (`expect`), the ones
that must not (`forbid`), the fixture tree the prompt is sent against, and whether it belongs to
the reduced suite (`quick`).

| File | Cases | What it is for |
|---|---|---|
| `should-fire.yaml` | 76 | Four per workflow: three English, one Spanish. |
| `should-not-fire.yaml` | 20 | Ordinary work and near misses. `expect: []`. |
| `multi-skill.yaml` | 7 | Requests where two or three workflows should fire together. |

One pass rule covers both polarities: a case passes when every expected workflow fired, no
forbidden one did, and — for a should-not-fire case — nothing fired at all. Keeping
under-triggering and over-triggering under a single rule is what stops a configuration being
reported as healthy while it fails one of them.

The prompts were written from the `SKILL.md` bodies and deliberately not from the frontmatter
descriptions, then reviewed a second time for description vocabulary, for reading like
specification prose rather than something a person would type, and for labels a cold reader would
disagree with. Nineteen of the hundred and three were rewritten by that review.

## The three arms

An arm is a configuration of the context this plugin's own hooks inject.

| Arm | Session-start catalogue | Standing instruction | Toggles |
|---|---|---|---|
| `none` | no | no | `COGNITIVE_POWERS_DISABLE_ACTIVATION`, `COGNITIVE_POWERS_DISABLE_ROUTER` |
| `instruction` | no | yes | `COGNITIVE_POWERS_DISABLE_ACTIVATION_INDEX` |
| `full` | yes | yes | none |

**Every run is checked against the arm it asked for.** These hooks degrade silently by contract, so
an arm that never took effect is otherwise indistinguishable from one that took effect and changed
nothing. A run whose injections disagree with its arm — missing, leaked, or delivered twice — is
recorded as incomplete rather than scored.

## What counts as an activation

Only an assistant `tool_use` block named `Skill` whose `input.skill` names an installed workflow.

Not the model saying it consulted one. Not a file read under the plugin tree. And emphatically not
a text match on the stream: with `--include-hook-events` the transcript carries the router's own
output, and that text contains the literal string `cognitive-powers:<name>`. A harness that scanned
for it would score its own instrumentation as the result.

Invocation is still a proxy for the workflow shaping the answer rather than proof of it.
`workedAfterFiring` records whether a tool ran *after* the first invocation, which separates a
workflow that then did something from one that was named and abandoned. It is reported as evidence
and no rate is computed from it.

Read it with one caveat. A should-fire run is stopped on the line carrying its Skill invocation, so
nothing can follow it and the field is always false there. It is informative only on runs allowed to
finish — misses and negatives.

## Reading a result

A rate is a property of complete observations only. A cohort with no complete run has **no rate at
all** and says so, rather than serializing `0.0` and being read as a workflow that never fires.
Every reported rate carries its `complete` and `incomplete` counts, because a rate over three runs
and a rate over thirty look identical once the denominator is dropped.

`flippedCases` lists the cases that passed some repetitions and not others. Those are the ones
whose outcome is not yet a property of the configuration.

## Cost

One invocation per (arm, case, repetition). A should-fire run stops as soon as its expectation is
met; a should-not-fire run is always paid in full, because a workflow that has not been invoked yet
is not a workflow that will not be invoked. `--max-cost-usd` bounds each run and
`--timeout-seconds` bounds each wall clock.

Measured on a 30-invocation pilot against `sonnet`: **$0.35 per should-not-fire run**, about
**$0.70–0.97** for a should-fire run that never fires, and effectively nothing for one that fires
early. Roughly 40 seconds per run.

`--workers` runs up to four sessions at once (default 3, `1` for sequential). Each already has its
own workspace, so the ceiling is the provider's patience rather than isolation. A run the provider
throttles is waited out with exponential backoff and retried — **never scored as a case failure**,
because a rate limit says nothing about whether the workflow would have activated. A run still
throttled after the last attempt is recorded as incomplete: absent from the denominator rather than
counted as a miss. `retriedRuns` and `rateLimitedRuns` are reported beside the rates, because a
matrix that needed forty retries was measured against a busy provider.

Raw transcripts land in `evals/artifacts/` only with `--keep-transcripts`, and that directory is
git-ignored: a transcript carries the whole session, including anything the fixture and the
operator's environment put in front of the model.
