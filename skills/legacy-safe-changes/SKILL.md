---
name: legacy-safe-changes
description: Land a change in code that has no tests without altering what it silently does - locate the change points, find a seam where behavior can be sensed or substituted, break the dependency with sprout and wrap moves so the class becomes testable, pin current behavior with characterization tests, then make the change inside that safety net. Use when the task involves editing untested code, a module with no coverage that everyone avoids, or a change that must not break callers nobody can enumerate.
when_to_use: Use when a change must land where test coverage is absent and behaviour is defined only by what the code currently does. The net comes first, the change second: characterization tests record today's actual output before anything moves.
---

# Legacy-Safe Changes

Untested code is defined by what it does, not by what anyone says it does.
The order below is the protocol: no step edits the code before the step that
protects it. Cleanup for its own sake belongs to `refactor-cleanly`; this
workflow exists to land a requested change where no net exists yet.

## 1. Identify the change points

List the exact functions, branches, and call sites the requested change must
touch. The list is the deliverable of this step - written down, not held in
mind - because every later step is scoped by it. Widening the list mid-change
is a finding to report, not a silent expansion.

## 2. Find the seams

For each change point, find the nearest place where behavior can be observed
or substituted without editing the code under change: a parameter that can
carry a test double, a constructor argument, an import boundary, an
overridable method, a module boundary. Prefer the seam closest to the change
point that a test can reach. If no seam exists within reach, that fact goes in
the report before any dependency is broken to create one.

## 3. Break the dependency

Use only named moves, and the smallest one that opens the seam:

- **Sprout method / sprout class** - new behavior goes into a new, tested unit
  that the old code calls; the old body is barely touched.
- **Wrap method** - the existing body keeps its behavior under a new name; the
  original name becomes a wrapper that adds the new step before or after.
- **Extract interface / parameterize constructor** - a hard-wired collaborator
  becomes replaceable so a test can substitute it.

Each move is mechanical and individually revertible. Record which move opened
which seam. Do not redesign while breaking a dependency - the goal is a
sensing point, not better structure.

## 4. Characterize current behavior

Write tests that pin what the code actually does today, fail-first:

1. Write an assertion you expect to fail against a value you invented.
2. Run it; read the actual value from the failure output.
3. Pin the actual value into the assertion and see it pass.

The recorded value is the spec, even when it looks wrong. A surprising output
gets a note in the report, never a silent correction: changing it is a
behavior change and needs its own authorization. Cover each change point with
enough characterization that an accidental behavior shift would turn a test
red.

## 5. Make the change

Only now edit. Work inside the net: the requested change lands in the
sprouted or wrapped units where possible, and the characterization suite runs
after each coherent step. A red characterization test means the change altered
something it was not authorized to alter - revert the step, do not adjust the
test.

## 6. Verify preservation

Run the full characterization suite plus any pre-existing tests. Every test
still green except those the change was explicitly authorized to update; each
intentional update is named in the report with its before and after value. The
report lists the change points, the seams and moves used, the surprising
behaviors recorded, and any surface that remains unprotected.

## Pause points

DO-CONFIRM: work from judgment, then stop at each point and confirm every
item. An unconfirmed item goes in the report, never silently past it.

**Before touching code**
- Change points written as an explicit list of functions and call sites.
- A seam identified for each change point, or its absence reported.
- Only named dependency-breaking moves planned, smallest first.

**Before the change itself**
- Characterization tests pinned to observed output, each seen red then green.
- Surprising recorded behaviors noted for the report, not corrected.
- Net turns red on an accidental behavior shift at every change point.

**Before claiming done**
- Full suite green; every intentional test update named with before and after.
- Requested change landed inside sprouted or wrapped units where possible.
- Unprotected surfaces named explicitly in the report.
