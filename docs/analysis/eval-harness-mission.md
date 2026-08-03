# Eval harness mission

Branch `feature/eval-harness`, cut from `main` after the activation mission merged. Merged back in
`3f2a708`; the branch is deleted and `main` is the only branch that exists.

This document is the handoff. It is written so that someone who has never seen this work — another
session, another person — can tell what is built, what is not, and what to run next, without reading
the diff. **The harness is finished and the measurement it exists to produce has not been taken.**
That is the single fact that matters most here, and every section below is arranged around it.

## What the mission asked

Three things. Build something that measures whether workflows actually fire on requests phrased the
way requests arrive; gate it in CI so the gap the activation mission closed cannot reopen silently;
and settle the open question that mission left behind — whether the session-start index earns its
743 tokens over the standing instruction alone.

[skill-activation-mission.md](skill-activation-mission.md) records the mission that produced that
question. Its "A finding that argues against part of the design" section is the reason the third
item exists.

## Where the work stands

| Phase | Deliverable | State |
|---|---|---|
| 0 | Reference clones read, both registered in `integrations/catalog.json` | done |
| 1 | [harness-decision.md](harness-decision.md) — verdict **BUILD** | done |
| 2.1 | Case corpus, 103 cases | done |
| 2.2 | Runner driving the real `claude` CLI in isolated temp workspaces | done |
| 2.3 | Three arms, each verified against the injections it actually received | done |
| 2.4 | Scoring, JSON and Markdown reports, paired inference | done |
| 2.5 | `evals/run.ps1` wrapper | done |
| 2.6 | `.github/workflows/activation-eval.yml` | done, never run against a credential |
| 2.7 | **Index decision: keep the 743-token catalogue, or cut to the instruction** | done — cut, on the number |
| 3 | Full matrix per arm, [activation-baseline-v1.md](activation-baseline-v1.md) | done |
| 4 | This document, and the 1.9.0 CHANGELOG entry covering the harness | done |

Everything in the `done` rows is committed on `main` and covered by the offline gate.

2.7 and 3 closed on 2026-08-03. Two arms over the whole corpus at one repetition found `full` and
`instruction` **equivalent** within the declared 0.10 margin over 61 paired trials, so the catalogue
stopped shipping. What that measurement did *not* reach is listed at the end of the baseline and is
not small: no `none` control, one model, one repetition, nothing on Codex, and a multi-workflow
cohort confounded by the per-invocation budget.

## What is built

`evals/activation_core/` is eleven modules. The split is not organisational: everything that can be
decided without a provider lives apart from the part that spawns one, so the offline gate covers the
judgement even though it can never cover the measurement.

| Module | What it decides |
|---|---|
| `yamlite.py` | The YAML subset the corpus is written in. Hand-written because the runtime has no dependencies and may not grow one. |
| `cases.py` | Case schema, selection, and the rule that a case passes only when every expected workflow fired and no forbidden one did. |
| `arms.py` | The three configurations, as environment toggles. |
| `fixtures.py` | Four generated workspace trees. Generated in-process, never read from the repository. |
| `session.py` | The one place that spawns `claude`. Timeout watchdog, process-tree kill, rate-limit backoff. |
| `transcript.py` | Structural activation detection and arm verification. |
| `scoring.py` | Rates, always with their `complete` / `incomplete` denominators. |
| `inference.py` | Paired comparison: exact McNemar, Wilson intervals, Agresti-Min adjusted difference, four-valued verdict. |
| `runner.py` | Interleaved execution, worker pool, early stop on a decided verdict. |
| `report.py` | Markdown. |

Three invariants in there are load-bearing and easy to break by accident:

**Detection is structural only** — an assistant `tool_use` block named `Skill` whose `input.skill`
names an installed workflow. Never a text scan. The router injects the literal string
`cognitive-powers:<name>` into the same stream, so a substring match would score the harness's own
instrumentation as the result.

**A run whose injections disagree with its arm is incomplete, not zero.** These hooks degrade
silently by contract. An arm that never took effect is otherwise indistinguishable from an arm that
took effect and changed nothing.

**A cohort with no complete run has no rate at all.** It says so rather than serialising `0.0`,
which would read as a workflow that never fires.

### The corpus

103 cases in `evals/cases/`: 76 should-fire (four per workflow — three English, one Spanish), 20
should-not-fire including near misses, 7 multi-workflow. 25 Spanish overall, 29 marked `quick`, and
the quick suite touches all nineteen workflows.

Prompts were written from the `SKILL.md` **bodies**, deliberately not from the frontmatter
descriptions. The routing benchmark's prompts were written from those descriptions, which is exactly
why it cannot see a workflow that never fires; writing this corpus the same way would have
reproduced the blind spot in the corpus meant to close it. Nineteen of the 103 were rewritten on a
second review pass for description vocabulary, for reading like specification prose, and for labels
a cold reader would dispute.

`--validate-only` refuses a workflow with fewer than three should-fire prompts. A new workflow
nobody wrote cases for is reported as unmeasured rather than scoring nothing.

### What makes the comparison affordable

The naive shape — whole corpus, three arms, three repetitions — is 927 invocations, and most of them
are spent after the answer has stopped changing. Three things cut it:

- **Arms run interleaved on the same cases**, so they are *paired*. Only trials where the arms
  disagreed carry information, and case difficulty drops out. Interleaving also stops wall-clock
  position becoming a property of an arm: arm-major order attributes an hour of provider throttling
  to whichever arm happened to be running.
- **The run stops when every arm pair has a verdict** — never on a rate that looks good so far.
  `--no-stop-when-decided` turns it off for per-skill rates.
- **More cases beat more repetitions** for this question. Repetitions of one case are correlated;
  distinct cases are independent pairs. Whole corpus at one repetition is 206 invocations and 103
  pairs; the quick suite at three arms and three repetitions is 261 invocations and ~29 pairs.

## What has not happened

**The CI job has still never run against a provider credential.** The free `corpus` job is
exercised; the paid `measure` job skips with a notice when `ANTHROPIC_API_KEY` is absent, which is
every run so far. Its thresholds are no longer guesses — the baseline moved the floor to 0.75 and
left the ceiling at 0.15 with its arithmetic recorded — but a threshold nothing has ever been run
against in CI has not been exercised, only calibrated.

The baseline was taken on a workstation rather than through that job, so the two paths have never
been shown to agree.

## How to resume

Free, spawns nothing, confirms the tree is intact:

```powershell
& $python evals/run_activation_eval.py --validate-only --full
```

The run that answered 2.7, kept here because it is the shape any re-measurement takes — two arms,
whole corpus, one repetition, stopping as soon as the comparison is decided:

```powershell
./evals/run.ps1 -Full -Arm instruction,full -Reps 1 -EquivalenceMargin 0.10 -JsonOutput C:\tmp\activation.json
```

Cost and time, from the pilot's measured durations at three workers: **$0.35** per should-not-fire
run, **$0.70–0.97** for a should-fire run that never fires, effectively nothing for one that fires
early and is stopped on the line carrying its invocation.

| Outcome | Trials needed | Wall clock |
|---|---|---|
| The index clearly wins | ~12 | ~4 min |
| The index wins modestly | ~32 | ~10 min |
| The arms agree exactly | ~18 | ~5 min |
| The arms differ only by noise, margin ±0.10 | ~80 | ~24 min |
| Full 206 invocations, no early stop | 103 | ~31 min |

What it actually cost, measured: 132 invocations before the stop fired, plus 54 for the negative
pool the stop had skipped, over about 80 minutes of wall clock. Add the negative pool to every row
above — it now runs whether the stop fires or not, which is the point of the fix the baseline
records, and it is the part the cheap outcomes do not get out of.

**Proving equivalence is the expensive outcome, and it has a ceiling.** At ±0.15 it needs ~38
trials; at ±0.10, ~80; at ±0.05, ~310 — more trials than the corpus has cases. With 103 cases,
**±0.10 is the tightest margin affordable at one repetition**. A claim that the index contributes
less than five points cannot be made from this corpus without growing it or adding repetitions.
The verdict for that case is `not-proven`, and it is reported rather than rounded to "no
difference", because an underpowered run is not a finding.

## Open items

1. **Invocation is a proxy for the workflow shaping the answer, not proof of it.**
   `workedAfterFiring` separates a workflow that then did something from one named and abandoned,
   but it is reported as evidence and no rate is computed from it — and it is always false on a
   should-fire run, which is stopped at its invocation. It is informative only on misses and
   negatives.
2. **The multi-workflow cohort is unmeasured, not measured badly.** The baseline found every one of
   the runs it discarded as `claude exited 1` to be a multi-workflow run at or above the
   `--max-cost-usd 0.75` ceiling. The budget is cutting off exactly the sessions that would have to
   reach a second workflow, and the harness files that under the same reason as a crash. Give
   budget termination its own reason before believing any number from this cohort.
3. **One misroute reproduces under every configuration tried.**
   `refactor-cleanly-coupon-rounding-copies` drew `legacy-safe-changes` under both arms of the
   baseline. That is a property of the listings rather than noise, and it is the pair 1.7.4 already
   had to separate once — `benchmarks/skill_routing_cases.json` has the `outranks` pin mechanism for
   exactly this, and naming the wrong workflow costs more than naming none.
4. **Nothing has been measured on Codex.** The harness spawns `claude` and detects a `Skill` tool
   use, which Codex has no equivalent of. Every rate in the baseline is a Claude Code rate.
5. **The harness measures activation, not outcome.** A configuration that fires the right workflow
   and produces worse work would score perfectly. That is a different mission.
6. **Duplicate hook execution is unresolved.** Claude Code loads both hook manifests. The arm
   verifier records a doubled injection as incomplete, so it cannot silently corrupt a
   measurement — but it does mean some runs will be discarded rather than scored.
