---
name: diagnose-systematically
description: Diagnose unclear, intermittent, or recurring defects with reproduction, falsifiable hypotheses, instrumentation, and regression evidence. Use when the cause is unknown.
disable-model-invocation: true
---

# Diagnose Systematically

Find the cause through observable evidence. Preserve the request mode: diagnosis alone does not authorize a fix.

## 1. Establish the signal

Read the relevant project guidance, callers, tests, logs, and exact reported symptom. Build the smallest agent-runnable command that can detect that symptom. Read [feedback-loops.md](references/feedback-loops.md) when choosing or judging the signal.

Do not form a root-cause conclusion until the signal has reproduced the reported failure. If no viable signal can be built, report what was attempted and request the smallest missing artifact or environment access.

For a browser-visible symptom in a project with an existing Playwright setup, invoke `verify-web-behavior` to build or run the smallest user-visible reproduction and retain failure evidence.

When the required path is unknown or an external interface has drifted, invoke `explore-web-adaptively` only to discover the flow, then return to Playwright for the symptom-specific signal.

## 2. Reproduce and minimize

Run the signal enough times to distinguish deterministic failure from intermittency. For a flaky defect, measure a reproduction rate and increase it with controlled stress, repetition, or timing changes.

Remove one input, dependency, configuration element, or step at a time. Keep a removal only when the same symptom still occurs. Stop when every remaining element is load-bearing.

## 3. Test hypotheses

Write up to five credible falsifiable hypotheses; never add filler to reach a quota. Give each one a prediction and falsifier that distinguish it from the others. Probe one variable at a time, using a debugger or focused instrumentation at the boundary where predictions diverge.

Keep one investigator for a focused defect. After the symptom is reproduced, read [investigation-lanes.md](references/investigation-lanes.md) when several components or plausible seams remain, the regression window is uncertain, or intermittency creates independent probes. Run the cheapest available discriminating probe before delegating. Parallel lanes remain read-only; the main agent owns synthesis and any authorized fix.

After the symptom-specific signal is red, use `solve-efficiently`'s semantic-navigation workflow when a fresh CodeGraph index can expose the call path or consumers around that boundary. Do not let graph exploration replace reproduction.

Tag temporary instrumentation with one unique marker so it can be removed deterministically. For performance regressions, capture a timing or profiler baseline before changing code.

## 4. Fix only when authorized

If the user requested a fix, convert the minimized reproduction into a regression test at the highest public seam that exercises the real failure. For a durable session, use `run-red` before changing source and `run-green` afterward with the exact same command.

If no correct test seam exists, state that limitation instead of adding a shallow test that cannot catch the defect.

## 5. Close with evidence

Re-run the original unminimized signal and the regression test. Remove tagged instrumentation and throwaway harnesses. Report the supported cause, the discriminating evidence, the checks run, and any surface that remains unverified.
