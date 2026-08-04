# Adaptive routing

Read this reference only when task size, sequencing, or delegation is unclear.

## Choose the work mode

- **Answer**: inspect enough evidence to respond; do not mutate external state.
- **Diagnose**: isolate and explain the cause; invoke `diagnose-systematically` when reproduction or causality is non-trivial, and do not implement unless requested.
- **Change**: implement, verify, and hand off the requested result.
- **Monitor**: observe the specified state until the terminal condition or a real blocker.

If a request mixes modes, preserve their order. For example, diagnose before changing, then verify the change.

For web UI behavior, route reproduction and executable verification through `verify-web-behavior` when the target already has Playwright configured. Keep ordinary code inspection and non-browser unit tests in their native workflow.

Route unfamiliar, cross-site, or layout-drifted browser discovery through `explore-web-adaptively` only when deterministic Playwright steps are not yet known. Handoff back to `verify-web-behavior` before claiming behavior.

Route interface creation, redesign, screenshot matching, responsive visual work, and design-quality claims through `design-intentionally`. Its intent and visual receipts complement Playwright; they never replace behavioral assertions.

## Size the workflow

### Direct

Use for one obvious action with low risk and a clear verification method. Do not create a plan or delegate.

### Focused

Use when one component and its nearest tests are involved. Keep a short internal sequence: inspect, change, verify.

### Cross-cutting

Use when behavior crosses modules, tools, data sources, or external state. Create an explicit plan with one observable outcome per step. Re-evaluate the plan when evidence changes the implementation path.

## Address the registered role

A plan assigns a `role`; the host is what turns that role into an agent with a
tool set. Claude Code registers every shipped role under a plugin-scoped name —
`cognitive-powers:executor`, `cognitive-powers:test-writer`,
`cognitive-powers:verifier`, `cognitive-powers:investigator`,
`cognitive-powers:researcher`, `cognitive-powers:reviewer` — and spawning one of
those is what makes the role's constraints real: `investigator`, `researcher`,
and `reviewer` cannot write because their tool sets refuse the edit tools, and
no role can spawn descendants because none is granted `Agent`.

Spawning a general-purpose worker and naming the role in its prompt leaves every
one of those constraints to prose. Codex registers no agents from the plugin
root and falls back to built-in workers; there the contracts still apply, but
they are only described, so state which worker actually ran. Never make a
durable criterion depend on a role being discoverable.

## Decide whether to delegate

After bounded discovery, represent candidate work as explicit units and call the
canonical orchestration runtime. Discover its versioned input with
`scripts/orchestration_policy.py --agent-plan-template 2 --json`; do not inspect
the runtime source to reverse-engineer the contract. Each unit names its role, objective, minimum
context, owned paths, dependencies, permissions, expected output, check, stop
conditions, RED eligibility, readiness, distinct output, and delegation depth.

The automatic conservative controller applies these gates:

- `solo`: fewer than two independent ready units, unclear boundaries, a cheaper
  local discriminator, no worker slot, invalid signals, exhausted retry, or a
  write plan without authority and validated disjoint ownership.
- `parallel-read-only`: two or more independent investigation, research, or
  review outputs, capped at three workers and the host's available slots.
- `parallel-packets`: authorized executors own disjoint ready packets. The main
  agent integrates them and a fresh verifier runs afterward.
- `staged-verify`: test writer before executor when a real RED target and
  separate ownership exist, or a fresh read-only verification wave after a
  delegated, durable, release-critical, or quality-claiming result.

Depth two is descriptive: the main agent creates all assignments. Every
assignment sets `may_spawn=false`; depth-two work is read-only, and no worker
may verify its parent. A failed assignment gets at most one retry after the
failure is classified. Then the main agent takes ownership or reports a blocker.

Validate worker responses with the runtime's worker-result contract. Require
status, changed paths, actual argv and exit codes, blockers, and remaining
risks; an omitted or invented check is not evidence.

Treat planner selection and host execution as different facts. A fresh plan is
`planned` and has no executed mode. If delegated work is absorbed by the main
agent, the host receipt records `executed_mode=solo`, `outcome=degraded`, and the
cause; it must not count as completed delegation. Include planning and
coordination cost in the benefit test and abstain when that overhead dominates.

Do not delegate tightly coupled edits, trivial lookups, or work whose result cannot be integrated without rereading the same context. Give each worker the raw task and minimum needed context; do not leak the expected conclusion into an independent validation.

## Stop conditions

Stop discovery when all are true:

1. The affected boundary is identified.
2. The change or answer is supported by current evidence.
3. A meaningful verification target is known.
4. Further exploration is unlikely to change the next action.

## Route the handoff

Use `communicate-efficiently` after the work mode and evidence are settled. Routine progress and low-risk handoffs may be compact. Diagnoses and consequential choices retain their causal context. Irreversible or order-sensitive instructions remain explicit.
