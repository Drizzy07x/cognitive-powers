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
| 2.7 | **Index decision: keep the 743-token catalogue, or cut to the instruction** | **not measured** |
| 3 | Full matrix per arm, `activation-baseline-v1.md` | **not run** |
| 4 | This document | done |

Everything in the `done` rows is committed on `main` and covered by the offline gate. Nothing in the
two bold rows has a number behind it.

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

**The measurement.** No arm has been run over the corpus. There is no
`activation-baseline-v1.md`, no activation rate for any configuration, and no answer to 2.7. The
index still ships because nothing has yet shown it should not — which is not the same as evidence
that it should.

What does exist is a 30-invocation pilot, which is how the cost figures below were obtained and how
six defects in the harness were found. It is not a baseline and was never scored as one.

**The CI job has never run against a provider credential.** The free `corpus` job is exercised; the
paid `measure` job skips with a notice when `ANTHROPIC_API_KEY` is absent, which is every run so
far. Its floor (0.70) and false-positive ceiling (0.15) are declared defaults, not calibrated ones —
the calibration is the baseline that has not been taken.

**1.9.0 owes a CHANGELOG entry.** `main` declares 1.9.0 and now carries this harness, but the 1.9.0
section describes only the activation mission. The entry should be written before that version is
published.

## How to resume

Free, spawns nothing, confirms the tree is intact:

```powershell
& $python evals/run_activation_eval.py --validate-only --full
```

Then the run that answers 2.7. Two arms, whole corpus, one repetition, stopping as soon as the
comparison is decided:

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

**Proving equivalence is the expensive outcome, and it has a ceiling.** At ±0.15 it needs ~38
trials; at ±0.10, ~80; at ±0.05, ~310 — more trials than the corpus has cases. With 103 cases,
**±0.10 is the tightest margin affordable at one repetition**. A claim that the index contributes
less than five points cannot be made from this corpus without growing it or adding repetitions.
The verdict for that case is `not-proven`, and it is reported rather than rounded to "no
difference", because an underpowered run is not a finding.

Whichever way it lands, the result goes in `activation-baseline-v1.md` and the 2.7 decision follows
it: if `instruction` and `full` are equivalent within the declared margin, the session-start index
comes out and `hooks/skill_activation.py` keeps only `standing_message()`.

## Open items

1. **2.7 is unanswered.** Above.
2. **Invocation is a proxy for the workflow shaping the answer, not proof of it.**
   `workedAfterFiring` separates a workflow that then did something from one named and abandoned,
   but it is reported as evidence and no rate is computed from it — and it is always false on a
   should-fire run, which is stopped at its invocation. It is informative only on misses and
   negatives.
3. **The CI thresholds are guesses until a baseline exists.** A floor of 0.70 that nothing has been
   measured against can fail a healthy configuration or pass a broken one.
4. **The harness measures activation, not outcome.** A configuration that fires the right workflow
   and produces worse work would score perfectly. That is a different mission.
5. **Duplicate hook execution is unresolved.** Claude Code loads both hook manifests. The arm
   verifier records a doubled injection as incomplete, so it cannot silently corrupt a
   measurement — but it does mean some runs will be discarded rather than scored.
