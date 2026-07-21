---
name: solve-efficiently
description: Use for cross-file implementation, reproduced defects, bounded primary-source decisions, or authorized real-host workflows. Do not use for obvious one-file edits.
---

# Solve Efficiently

Choose the shortest path that proves the requested outcome. Read no other
Cognitive Powers skill unless the first focused inspection leaves the boundary
genuinely unclear.

## Current-source decision

When the checkout provides a decision brief, source policy, and primary-source
files:

1. In one tool call, read the brief, policy, applicable `AGENTS.md`, and every
   supplied source.
2. Build an internal requirement matrix: each question or constraint maps to
   the exact claim needed, its source, version/date, and any unresolved fact.
3. Answer directly. Cite every decision-critical claim at claim level using the
   source identifier or primary URL required by the policy.
4. Cover every explicit requirement, including protocol headers, compatibility,
   defaults, retired behavior, and operational constraints.
5. Distinguish publication, rollout, deprecation, and retirement dates. Never
   convert one into another or invent a schedule the source does not state.
6. State uncertainty explicitly when the sources do not establish a fact.

Use the brief's numbered order. Answer in the same number of compact numbered
items, without a preamble or restating the brief, and stay under 350 words unless
the brief explicitly requires more. Keep each claim and its citation in the same
paragraph; do not separate them with a table, list, or fenced block. For
requested protocol values or headers, state every exact name, value,
requirement, and source marker together in one prose sentence; do not format
protocol values as separate lines. Prefer the primary source's own terminology
for status and behavior instead of replacing it with a looser synonym. Express
obligations and lifecycle states with explicit labels such as `must`,
`required`, `supported`, `unsupported`, or `retired`, not only indirect
paraphrases. Before finalizing, silently check that every row in the requirement
matrix appears once with its source marker.

Do not browse when the supplied policy says the frozen primary sources are
complete. Do not load another skill, run repository discovery, or make multiple
source-reading passes.

## Bounded implementation

When a contract or reproduction identifies a small source-and-test boundary:

- Additive feature: discovery, patch, verification.
- Existing defect: discovery, focused reproduction, patch, verification.

The discovery call reads applicable instructions, the named contract or
reproduction, matched implementation, and nearest tests together. Reproduce
only an existing defect. Patch one coherent boundary. Verify the public behavior
with the focused test and at most one distinct affected suite. Stop after it
passes; do not add Git, compile, parser, diff, or unrelated test calls.

## Real-host workflow

When `WORKFLOW.md` and `HOST_TOOL.md` are supplied, read them with applicable
instructions in one call. Create the single declared public-action plan, execute
it once, then run the fresh observation. Use only documented labels, roles,
clicks, fills, selects, and keys. Do not call private APIs, edit persisted state,
inject scripts, substitute a mock, or rerun a successful workflow. Report the
exact action and observer receipt hashes.

## Escalation and handoff

Read `../../skills/solve-efficiently/SKILL.md` only if focused inspection still
leaves module boundaries, cause, or verification genuinely unclear. Otherwise
report the result, exact checks already run, and any untested surface without
re-reading files.
