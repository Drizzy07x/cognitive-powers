---
name: solve-efficiently
description: Execute cross-file or complex multi-module work; fix a defect described by a supplied reproduction while preserving unrelated behavior; answer a bounded technical decision using supplied current primary sources; or complete a specified workflow through an authorized real host, with progressive context and verified completion. Use when a public contract spans implementation, persistence, CLI, browser, or tests, boundaries need discovery, repository context payload must be reduced, or Cognitive Powers is requested. Abstain only from obvious one-file changes with a nearby test.
---

# Solve Efficiently

Optimize for a correct, verified outcome per unit of context. Do not reduce tokens by skipping evidence, and do not add process that a simple task does not need.

For a bounded technical decision with a supplied brief, version constraints, and primary-source policy, use one evidence batch: read the brief/policy, inspect only the named version-matched primary sources, preserve exact URLs and caveats, then separate verified facts, inference, recommendation, and uncertainty. Do not load another workflow, use Context7, run generic discovery, or test an implementation that was not requested.

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

- Select `focused`, `standard`, or `durable` with `scripts/orchestration_policy.py` when intensity is not obvious. Preserve its reasons and abstentions; do not infer permission to retrieve memory or delegate from intensity alone.
- Focused task: inspect the target and its nearest tests, then execute without durable state, receipts, memory retrieval, or coordination overhead.
- Standard task: use progressive context and nearby verification without durable state unless a durable signal appears.
- Durable task: use external state and evidence receipts when work spans turns, risks compaction, must be resumable, or explicitly requires durable evidence.
- Independent investigations: delegate or batch them only when the expected information gain exceeds the coordination cost.
- Coupled edits: keep one owner to avoid conflicting patches and duplicated context.

Request mode and intensity are independent. A diagnosis remains investigation-only at every intensity; implement a fix only when the user authorized a change.

### Focused code-and-test fast path

When the request names a bounded behavior, the relevant module is easy to locate, and the nearest tests are available, keep the execution to one coherent pass:

1. In one discovery batch, inspect applicable instructions, repository state, the target source, and its nearest tests.
2. Apply one coherent patch after the behavior and verification target are clear.
3. Run the focused behavioral test once. Run one broader affected suite only when it covers a distinct regression boundary.
4. Hand off from the evidence already collected.

Do not invoke another skill merely to format a routine progress update or completion message. Do not repeat an unchanged search, re-read unchanged files, rerun an already-green command for reassurance, or clean generated caches unless the request or a failing check makes that cleanup material. If no repository instruction file is found in the bounded search, continue instead of searching for it again.

A local feature contract (including conventional `FEATURE*`, `TASK*`, `SPEC*`, `CONTRACT*`, or `README*` files) plus a small matched source-and-test set is already a clear boundary. Do not load more workflow guidance after finding it, and do not treat a shell quoting or command-formatting mistake as architectural ambiguity. Retry the narrow command directly. Prefer a direct language-level reproduction over dynamically quoted shell scripts, and after the conditional focused-plus-affected verification passes, do not add separate parser, compile, search, or file re-read calls.

For an additive feature whose local contract explicitly documents the gap, do not spend a separate pre-patch call proving the feature is absent. Use discovery, one patch, and one focused-plus-affected verification. For defects, prioritize conventional root `REPRODUCTION*`, `ISSUE*`, or `BUG*` files: read the reproduction and list source/test paths first, then read only exact referenced or identifier-matched files while reproducing. Never open or run an unrelated test framework merely because the repository is small.

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

For a routine progress update or completion report, render a compact handoff directly: lead with the outcome, state material changes, give the exact checks and results, and name any remaining limitation. Invoke `$communicate-efficiently` only when the user explicitly requests adaptive brevity, the communication is consequential enough that profile selection is genuinely ambiguous, or a provider-backed communication receipt is required. Omit a diary of routine tool calls.
