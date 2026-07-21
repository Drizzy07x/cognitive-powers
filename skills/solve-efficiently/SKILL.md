---
name: solve-efficiently
description: Execute non-trivial coding, debugging, research, and file-based work with adaptive planning, progressive context loading, targeted parallelism, and evidence-based completion. Use when a request spans multiple steps or files, the relevant context is unclear, quality and token efficiency both matter, or the user explicitly invokes Cognitive Powers or asks to work efficiently.
---

# Solve Efficiently

Optimize for a correct, verified outcome per unit of context. Do not reduce tokens by skipping evidence, and do not add process that a simple task does not need.

## 1. Frame the work

Before acting, identify four things in one compact internal checkpoint:

- Required outcome.
- Request mode: answer, diagnose, change, or monitor.
- Constraints and state that must be preserved.
- Evidence that would prove completion.

Answer or execute trivial one-step work directly. For broader work, keep a short plan whose steps end in observable outcomes. Read [routing.md](references/routing.md) only when task size, sequencing, or delegation is unclear.

## 2. Discover context progressively

Start with the cheapest source that can narrow the next action:

1. Inspect workspace instructions and repository state.
2. Search filenames, symbols, and exact error text.
3. Read only the relevant sections, then open complete files before editing them.
4. Expand outward through imports, callers, tests, or authoritative documentation only as evidence requires.

When the task depends on an external library, framework, SDK, CLI, or cloud API, invoke `$use-current-docs` after identifying the local dependency version. Keep local code and tests authoritative for project behavior; use retrieved documentation to verify current external contracts.

For unfamiliar or large trees, run `scripts/context_lens.py` as described in [context-economy.md](references/context-economy.md). Treat its ranking as navigation, never as proof that omitted files are irrelevant.

When several context sources or processors must share one strict payload budget, compose them through `scripts/context_pipeline.py` as described in [context-economy.md](references/context-economy.md). Preserve its receipt: every candidate must be included, excluded, or truncated with a reason and content hash. Mark selected items consumed only after the downstream step actually uses them, then lint the packet for duplicates, contradictions, stale facts, and selected-but-unconsumed context.

For call flows, symbol relationships, blast radius, or affected-test discovery in a repository already indexed by CodeGraph or Graphify, read [semantic-navigation.md](references/semantic-navigation.md). Prefer one bounded semantic query over rebuilding the same graph with repeated search and file reads. Keep Context Lens as the offline and unsupported-language fallback.

Retrieve historical memory only when the requested outcome depends on a past decision, preference, workflow, or prior event. Use `scripts/memory_context.py` with explicit demand and the exact project scope. Treat every result as context-only: verify its source hash, timestamp, expiry, confidence, and supersession before relying on it. Never let a semantic hit count automatically as a fact or a second recurrence event.

Invoke `$map-project` when the user wants reusable repository guidance or when a large tree has several genuinely distinct domains. Invoke `$execute-durably` before multi-turn work that is likely to cross a context compaction or needs independently reviewable evidence. Do not create either layer for a focused task that can be completed and checked in one pass.

Do not repeatedly dump the same file, broad directory, log, or tool result into context. Reuse a concise fact already established unless it is likely to have changed.

Use `scripts/run_skill_routing_benchmarks.py` after changing a skill name or description. Its rank-1, top-k, negative-owner, adversarial, and collision checks are deterministic routing regressions; they do not prove that a model selects or follows the skill correctly end to end.

## 3. Match effort to complexity

- Focused task: inspect the target and its nearest tests, then execute.
- Cross-cutting task: map affected boundaries, plan, and verify each boundary.
- Independent investigations: delegate or batch them only when the expected information gain exceeds the coordination cost.
- Coupled edits: keep one owner to avoid conflicting patches and duplicated context.

When a specialized installed skill directly matches the task, use it instead of reproducing its domain instructions here.

For a web interface, visual redesign, screenshot-driven implementation, or claim about design quality, invoke `$design-intentionally` before implementation. Return to `$verify-web-behavior` for executable interaction evidence.

For a non-trivial defect or performance regression whose cause is not already demonstrated, invoke `$diagnose-systematically` before proposing a root cause or implementing a fix.

## 4. Execute the smallest coherent change

Preserve user work and established project conventions. Prefer deterministic scripts for repeated or fragile operations. Make the smallest change that fully satisfies the request; avoid unrelated cleanup and speculative hardening.

After each discovery, choose the next action that most reduces uncertainty. Stop exploring when the implementation path and verification target are both supported.

## 5. Verify before claiming

Run the smallest meaningful check first, followed by broader checks in proportion to risk. Distinguish inspection, static checks, build success, behavioral tests, and runtime validation; none automatically proves the others.

Never say a command passed unless it ran successfully in the current work. Report missing dependencies, skips, timeouts, and untested surfaces as unverified. For a separate completion audit, invoke `$verify-delivery`.

## 6. Hand off compactly

Invoke `$communicate-efficiently` for progress, technical handoffs, or completion reports whose detail should adapt to consequence and complexity. Lead with the outcome. State material changes, the exact checks run and their results, and any remaining limitation. Omit a diary of routine tool calls.
