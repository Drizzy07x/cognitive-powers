# Adaptive routing

Read this reference only when task size, sequencing, or delegation is unclear.

## Choose the work mode

- **Answer**: inspect enough evidence to respond; do not mutate external state.
- **Diagnose**: isolate and explain the cause; invoke `$diagnose-systematically` when reproduction or causality is non-trivial, and do not implement unless requested.
- **Change**: implement, verify, and hand off the requested result.
- **Monitor**: observe the specified state until the terminal condition or a real blocker.

If a request mixes modes, preserve their order. For example, diagnose before changing, then verify the change.

For web UI behavior, route reproduction and executable verification through `$verify-web-behavior` when the target already has Playwright configured. Keep ordinary code inspection and non-browser unit tests in their native workflow.

Route unfamiliar, cross-site, or layout-drifted browser discovery through `$explore-web-adaptively` only when deterministic Playwright steps are not yet known. Handoff back to `$verify-web-behavior` before claiming behavior.

Route interface creation, redesign, screenshot matching, responsive visual work, and design-quality claims through `$design-intentionally`. Its intent and visual receipts complement Playwright; they never replace behavioral assertions.

## Size the workflow

### Direct

Use for one obvious action with low risk and a clear verification method. Do not create a plan or delegate.

### Focused

Use when one component and its nearest tests are involved. Keep a short internal sequence: inspect, change, verify.

### Cross-cutting

Use when behavior crosses modules, tools, data sources, or external state. Create an explicit plan with one observable outcome per step. Re-evaluate the plan when evidence changes the implementation path.

## Decide whether to delegate

Delegate only a bounded subproblem that can run independently while useful local work continues. Good candidates include separate source audits, independent reproduction, documentation lookup, and a clean-room verification pass.

Do not delegate tightly coupled edits, trivial lookups, or work whose result cannot be integrated without rereading the same context. Give each worker the raw task and minimum needed context; do not leak the expected conclusion into an independent validation.

## Stop conditions

Stop discovery when all are true:

1. The affected boundary is identified.
2. The change or answer is supported by current evidence.
3. A meaningful verification target is known.
4. Further exploration is unlikely to change the next action.

## Route the handoff

Use `$communicate-efficiently` after the work mode and evidence are settled. Routine progress and low-risk handoffs may be compact. Diagnoses and consequential choices retain their causal context. Irreversible or order-sensitive instructions remain explicit.
