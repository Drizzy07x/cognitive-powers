# Feature surface

The complete inventory of what Cognitive Powers ships. The
[README](../README.md) keeps the reading path a first visit needs; this file is
the detail behind it. Claims here are scoped deliberately: where a surface
validates a contract rather than an outcome, it says so.

## Fail-closed evidence and result-quality boundaries

Introduced in 1.4 and still in force:

- Legacy or non-finite evaluation data cannot prove end-to-end improvement; only a complete versioned v2 promotion protocol is claim-eligible.
- Context selectors must produce a one-to-one, budget-respecting receipt, and excluded context cannot be reported as consumed.
- Memory supersession is resolved before lexical filtering so an obsolete decision cannot reappear merely because its replacement uses different words.
- Release witnesses re-derive manifest identity, source inventory, validation commands, and live status instead of trusting mutable summary fields.
- Disposable installation checks cover declared assets, hook runtime files, and every validation component.
- Durable locks retain live owners, source fingerprints fail on unreadable files, WAL corruption fails closed, and capability transitions form a receipt chain.

## The broader surface

- A typed context pipeline with ordered providers and processors, strict character budgets, per-item inclusion receipts, and deterministic lint for duplicates, contradictions, expiry, and unused context.
- A routing benchmark for every installed skill with positive, negative-owner, adversarial-pressure, rank-1, top-k, and collision cases, plus the decision the host actually gets: the rate at which a positive is named, a bilingual off-domain corpus that must draw no suggestion at all, a Spanish corpus, and a ceiling on prompts routed to a workflow that does not own them. It does not claim end-to-end model improvement.
- Provider-neutral semantic navigation across an existing fresh CodeGraph index, a worktree-bound, structurally healthy, incrementally complete Graphify export, or bounded lexical fallback. Graphify probes expose counts and bounded diagnostics rather than the graph payload.
- Demand-only, project-scoped memory through native JSON or SQLite stores and an optional existing memU CLI, with source hashes, timestamps, confidence, expiry, supersession, snapshots, and guarded undo.
- An evidence-bound capability lifecycle: `observed`, `candidate`, `trial`, `active`, and `retired`, with distinct events, immutable checks, approval, implementation fingerprints, and rollback evidence.
- A reproducible research skill with frozen pre-registration, confirmatory/exploratory separation, claim-to-evidence binding, dead ends, pivots, and an independent verdict.
- Optional knowledge closeout across code, runtime, documentation, rules, memory, and workspace without implicit cleanup or memory writes.
- A versioned external capability catalog covering all audited sources with immutable SHAs, licenses, decisions, states, labels, and compare-and-swap updates.
- Passive adapters for Context Mode, Graphify, memU, Ruflo, Nacos, LobeHub exchange, and Obsidian that never install providers and always retain a native fallback.
- Durable coordination reports, a hash-bound release witness, and a paired evaluation gate that refuses unsupported improvement claims.
- Adaptive routing that avoids heavy process for simple requests and emits deterministic `solo`, `parallel-read-only`, `parallel-packets`, or `staged-verify` agent plans for non-trivial work.
- Intentional interface design from audience, brand, references, preservation boundaries, and context-sensitive variance, motion, and density controls.
- Greenfield and redesign workflows that reuse existing systems by default and never fabricate customers, metrics, screenshots, or product state.
- Typed visual-review receipts that bind design intent, Playwright evidence, mobile and desktop PNG renders, reviewer checks, and artifact hashes without pretending taste is objective.
- Durable `record-design` evidence that remains explicitly ineligible as behavioral verification.
- Adaptive `compact`, `normal`, and `explicit` communication profiles that preserve material facts, exact technical literals, limitations, and evidence.
- Provider-backed communication usage receipts and paired comparisons that refuse efficiency claims after failure, critical errors, or quality regression.
- Systematic defect diagnosis built around a tight, symptom-specific reproduction signal.
- Adaptive read-only investigation lanes that activate only after reproduction when independent probes justify their coordination cost.
- Progressive context discovery for large repositories.
- A bounded Context Lens that ranks relevant files and excerpts.
- Optional CodeGraph navigation for semantic exploration, symbol impact, and affected-test candidates when an existing index is fresh.
- Explicit lexical fallback when CodeGraph is absent, stale, incomplete, or bound to another worktree.
- Optional Playwright verification for real browser flows using normalized JSON results, traces, and hashed artifacts.
- Durable `record-web` evidence that copies every declared browser artifact and detects later tampering.
- Optional Quick Computer Use integration for persistent, guarded native Windows observation and input.
- Fail-closed QCU transcript normalization that rejects wrong focus, stale frames, busy/rejected input, missing real actions, unverified objectives, and premature finish.
- Durable `record-desktop` evidence that copies and hashes the QCU receipt and raw transcript before independent verification.
- Optional Skyvern discovery for unfamiliar, changing, or cross-site browser workflows.
- Fail-closed Skyvern-to-Playwright handoffs that cannot pass until deterministic actions and assertions replace the placeholder.
- Typed `record-navigation` receipts that remain explicitly ineligible as behavioral verification.
- Optional version-aware Context7 retrieval for current external library and API documentation.
- Compact hierarchical project maps for placing useful `AGENTS.md` guidance without duplicating the tree.
- Durable external work state with atomic writes, an authenticated checkpoint/delta ledger, fixed-size recovery state, source fingerprints, and resumable criteria.
- Dependency-aware work packets with exclusive path ownership, immutable argv checks, owned-path fingerprints, and an integration gate that remains separate from completion.
- A strict Markdown-to-work-packet compiler that rejects incomplete sections, shell-string checks, unsafe paths, ownership overlap, unknown dependencies, and cycles before state changes.
- Narrow executor, test-writer, and read-only verifier role contracts without fixed model versions; assignments carry objective, context, ownership, permissions, checks, and stop conditions, while worker results require actual command exits, blockers, and risks. Repository TOML agents improve development while installed-plugin workflows retain the same roles through skill instructions.
- Version-neutral prompt contracts with deterministic structural validation and an explicit semantic review for outcomes, success criteria, boundaries, permissions, tools, evidence, and stop conditions.
- Selective, cross-platform plugin hooks that record edit provenance outside the repository, chain ledger hashes, and warn when the latest edit lacks a current validation receipt.
- A bounded React and Next.js static performance review that reports detected framework versions and review candidates without claiming measured runtime improvement.
- Red/green regression receipts that require one unchanged command to fail before a source change and pass afterward.
- Real command receipts and copied artifact hashes that become stale when their source or reviewed evidence changes.
- Independent executor/verifier gates that block completion on failure, inconclusive evidence, or self-verification.
- A separate delivery audit with explicit evidence levels and independent Contract and Quality verdicts.
- Source-bound review synthesis that deduplicates exact issues, preserves reviewer conflicts, and selects security only for requested or material boundaries.
- Optional implementation-free domain glossaries kept separate from operational `AGENTS.md` guidance.
- Evidence-backed capability audits that distinguish repeated workflows from duplicated events, stale memory, and existing skills that should be updated instead.
- Deterministic skill validation for metadata, placeholders, local references, and UI invocation prompts.
- Deterministic validation, context-selection, and capability-audit benchmarks.
- A deterministic communication-contract benchmark that detects lossy compression without claiming end-to-end model improvement.

The current tests validate plugin structure, context selection, state transitions, real exit-code capture, evidence integrity, and completion gates. Historical bounded-run measurements are not claim-eligible unless their immutable provider receipts, evaluator identities, and source fingerprints are available with the checkout. No broad model-quality or token advantage is currently claimed.

## How the two hosts reach the same workflows

One source tree packages both hosts. Codex loads `.codex-plugin/plugin.json` and
`skills-core/`; Claude Code loads `.claude-plugin/plugin.json` and `skills/`.
Both expose the same three core workflows and the same sixteen specialized ones.
Only the invocation prefix differs: Codex uses `$name`, Claude Code uses
`/name`. A drift gate keeps the two manifest versions identical.

The two hosts reach the sixteen specialized workflows differently. Codex sees
only the three core entries and loads a specialized workflow from the core
router when the task directly matches. Claude Code lists all nineteen, because a
skill it does not list cannot be invoked by the model at all, and the core
workflows delegate to the specialized ones by name. Each description carries an
explicit `when_to_use` trigger contract stating both when the workflow applies
and when it does not, so the wider catalog does not turn into over-triggering;
the deterministic routing benchmark scores that combined listing text and
reports unchanged accuracy against the narrower surface.

Only the listing text, a description plus `when_to_use` per skill, is held in
context; a workflow body loads when it is used.

## Agent roles

Six agent roles register under plugin-scoped names: `cognitive-powers:executor`,
`cognitive-powers:test-writer`, `cognitive-powers:verifier`,
`cognitive-powers:investigator`, `cognitive-powers:researcher`, and
`cognitive-powers:reviewer`. A workflow addresses a role by that exact name,
because the registered definition is where the tool set lives and the tool set
is what enforces a contract a prompt can only describe.

None of them is granted `Agent`, so a worker cannot spawn its own workers and
depth one is a property of the tool set rather than a request. The last three
are the read-only roles in `scripts/orchestration_policy.py`, and the only ones
it will place at depth two; each refuses the edit tools rather than merely
omitting them.

The verifier withholds the edit tools and runs under `isolation: worktree`: it
keeps `Bash`, because verification means running real checks, and `Bash` is
exactly what a withheld-edit-tools list does not contain -- the disposable
checkout is what makes the read-only claim true of your tree. The investigator
takes the same pairing for the same reason; the researcher and the reviewer are
granted no execution tool at all, so nothing is left for a checkout to contain.

## Evidence MCP server

The plugin declares one MCP server, `cognitive-powers-evidence`, so a host that
cannot shell out can still read the durable store: `inspect_evidence_storage`,
`summarize_durable_session`, and `check_durable_session_schema`. Every published
tool is an inspection. Mutation stays on `work_state.py`, where the ownership,
lock, and independent-verifier gates live, and the server does not reimplement
the read path either -- each tool runs the canonical subcommand and returns its
JSON, because two implementations of one contract diverge and the one behind an
MCP boundary is the one nobody would notice diverging. The tool table is the
allowlist: a name that is not in it reaches no subprocess, so no argument a
caller supplies can select a mutating subcommand.

## Semantic index hook

A `SessionStart` hook refreshes the optional semantic index, so navigation reads
a graph that matches the worktree. It acts only when Graphify is already
installed, the working directory is a checkout, and an index is already there:
it never installs Graphify and never creates an index, so a repository you do
not own is left untouched. It also skips the rebuild when Git reports the
worktree unchanged since the last run. Set `COGNITIVE_POWERS_DISABLE_INDEX=1`
to turn it off, or `COGNITIVE_POWERS_INDEX_TIMEOUT` to change its bound.

## Orchestration policy and the agent-plan contract

For non-trivial work, `solve-efficiently` can evaluate an explicit planning
packet through the packaged runtime:

```powershell
& $python scripts/orchestration_policy.py --agent-plan <plan.json> --json
```

Use `--explain-agent-plan <plan.json>` for the same deterministic decision with
eligible, excluded, and completed units; dependencies and waves; reserved
capacity; consumed signals; reasons, abstentions, and pending gates. Explain is
read-only: it does not create state, launch agents, or call providers.

The versioned `agent_plan` contract reports its mode, content-bound plan and
assignment IDs, ordered dependency waves, permissions, abstentions, retry
record, stop conditions, and receipt policy. Version 2 requires executable RED
and verification evidence for delegated writes and supports plan-bound worker
and verifier results. Invalid semantic signals return an observable `solo`
plan. The existing v1 `--input`/`--cases` intensity interface and
`select_intensity()` remain compatible. One host slot always remains with the
main agent. The other slots are reused by ordered waves, so a verifier planned
for a later wave does not reduce an earlier worker wave. `spawn_count` is the
total number of planned non-verifier workers, `total_planned_agents` also
includes the verifier, and `max_concurrent_workers` is the largest
non-verification wave. A fresh verifier runs only after implementation workers
finish.

V2 worker results remain structurally readable without claim context, but they
are durable-claim-ineligible until an `execution_context` v1 binds the source
SHA-256, each assignment to its real actor identity, and a verifier to the
complete prior worker-result bundle. Every terminal result must include the
assignment's exact declared check. A write that legitimately changes nothing
uses the explicit `no-op` status, an empty changed-path list, a concrete reason,
and a successful required check; an empty `completed` write is rejected.

## Capability matrix

| Surface | Default offline | Optional dependency | May consume credits | Requires a real app or host for a live claim |
|---|---:|---:|---:|---:|
| Native routing, context, memory, durable state, and validation contracts | Yes | No | No | No |
| Context Mode, Graphify/CodeGraph, and memU adapters | Fallback only | Yes | Provider-dependent | No |
| Context7 current-document retrieval | No | Yes | Provider-dependent | No |
| Playwright browser verification | Fixture contracts only | Yes | No by itself | Yes |
| QCU desktop verification | Transcript contracts only | Yes | No by itself | Yes |
| Skyvern browser discovery | Normalization contracts only | Yes | Yes, depending on provider | Yes |
| Ruflo, Nacos, LobeHub, and Obsidian adapters | Declaration/detection only | Yes | Provider-dependent | Provider-dependent |

"Offline" means deterministic local contract or fixture validation. It does not
mean the associated external provider or user-visible workflow was exercised.

## Optional providers

Context7, CodeGraph, Playwright, QCU, and Skyvern are optional. Cognitive Powers
continues to work without these integrations. It never installs or initializes
them inside a target repository implicitly. QCU is never started merely because
its skill loads. Skyvern completion, QCU primitive success, graph-selected
tests, screenshots, traces, recordings, and generated candidates remain
supporting evidence until a relevant objective-level assertion passes.

External source decisions and immutable revisions live in
`integrations/catalog.json`. Discovery catalogs never install skills; candidates
must pass capability, provenance, routing, and behavioral gates first.
`integration_adapters.py` only detects existing providers unless `--execute`
explicitly authorizes a bounded version probe.
