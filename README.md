<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.png">
    <img src="assets/logo.png" alt="Cognitive Powers" width="720">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/Drizzy07x/cognitive-powers/actions/workflows/validate.yml"><img src="https://github.com/Drizzy07x/cognitive-powers/actions/workflows/validate.yml/badge.svg" alt="Validate"></a>
  <a href="https://github.com/Drizzy07x/cognitive-powers/releases/latest"><img src="https://img.shields.io/github/v/release/Drizzy07x/cognitive-powers" alt="Latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/Drizzy07x/cognitive-powers" alt="License: MIT"></a>
</p>

# Cognitive Powers

Cognitive Powers is a Codex and Claude Code plugin for executing non-trivial work with focused context and evidence-based verification.

Each release ships the evidence for its own claims. The release archive is built
twice and compared byte for byte, and the compatibility matrix carries 108
cells — three operating systems, two Python versions, and two Codex CLI
versions, each across nine lifecycle scenarios including upgrade, rollback,
CRLF/LF, symlinks, Unicode paths, corrupt state, and a checkout without Git —
every one populated from a receipt bound to that exact commit and run. A cell
with no receipt stays `unknown` instead of being assumed to work.

One source tree packages both hosts. Codex loads `.codex-plugin/plugin.json` and
`skills-core/`; Claude Code loads `.claude-plugin/plugin.json` and `skills/`.
Both expose the same three core workflows and the same sixteen specialized ones.
Only the invocation prefix differs: Codex uses `$name`, Claude Code uses
`/name`. See [Install in Claude Code](#install-in-claude-code).

## Quickstart: three flows

Use the smallest flow that fits the work:

1. **Focused solve** — invoke `$solve-efficiently` (`/solve-efficiently` in Claude Code) for a bounded implementation, diagnosis, or research task. It selects only the context and checks justified by the request, and its conservative controller activates host-native agents only after bounded discovery proves that independent work justifies the coordination cost.
2. **Durable execution** — invoke `$execute-durably` (`/execute-durably`) when work spans several steps, agents, or compactions. Criteria, command exits, source fingerprints, and independent verification remain external to the target repository.
3. **Delivery verification** — invoke `$verify-delivery` (`/verify-delivery`) with the original claim and relevant checkout. It reports Contract and Quality separately and does not turn missing or stale evidence into success.

These are prompt-level plugin flows; they do not install optional providers or authorize publication, live browser actions, or desktop input.

## Choose a skill

| Need | Skill | Use it when |
|---|---|---|
| Bounded implementation, diagnosis, or source-backed decision | `$solve-efficiently` / `/solve-efficiently` | The work can be narrowed to a coherent source-and-test boundary. |
| Resumable multi-step execution with durable evidence | `$execute-durably` / `/execute-durably` | Work may span agents, turns, or compactions and needs external receipts. |
| Independent audit of an existing completion claim | `$verify-delivery` / `/verify-delivery` | Implementation has stopped and the claim must be checked against current evidence. |

For updates, lock recovery, state-schema inspection, validation receipts, and
the local-usage-counter abstention, use the
[Operational guide](docs/operations.md).

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

The two hosts reach the other sixteen specialized workflows differently. Codex sees only these three core entries and loads a specialized workflow from the core router when the task directly matches. Claude Code lists all nineteen, because a skill it does not list cannot be invoked by the model at all, and the core workflows delegate to the specialized ones by name. Each description carries an explicit `when_to_use` trigger contract stating both when the workflow applies and when it does not, so the wider catalog does not turn into over-triggering; the deterministic routing benchmark scores that combined listing text and reports unchanged accuracy against the narrower surface.

Version 1.4 adds fail-closed evidence and result-quality boundaries:

- Legacy or non-finite evaluation data cannot prove end-to-end improvement; only a complete versioned v2 promotion protocol is claim-eligible.
- Context selectors must produce a one-to-one, budget-respecting receipt, and excluded context cannot be reported as consumed.
- Memory supersession is resolved before lexical filtering so an obsolete decision cannot reappear merely because its replacement uses different words.
- Release witnesses re-derive manifest identity, source inventory, validation commands, and live status instead of trusting mutable summary fields.
- Disposable installation checks cover declared assets, hook runtime files, and every validation component.
- Durable locks retain live owners, source fingerprints fail on unreadable files, WAL corruption fails closed, and capability transitions form a receipt chain.

The broader Cognitive Powers surface includes:

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

Context7, CodeGraph, Playwright, QCU, and Skyvern are optional. Cognitive Powers continues to work without these integrations. It never installs or initializes them inside a target repository implicitly. QCU is never started merely because its skill loads. Skyvern completion, QCU primitive success, graph-selected tests, screenshots, traces, recordings, and generated candidates remain supporting evidence until a relevant objective-level assertion passes.

## Install from the GitHub repository

The commands in this section are release-facing: the installer resolves the
named tag to an immutable commit first, and when that tag does not exist the
preflight fails without changing the profile.

The installer resolves the release tag to an immutable commit through GitHub CLI and configures Git credentials with it, so `gh` must be installed and authenticated even though the repository itself is readable without it. Install or update Cognitive Powers with one PowerShell command:

```powershell
& ([scriptblock]::Create((gh api 'repos/Drizzy07x/cognitive-powers/contents/install.ps1?ref=v1.8.1' -H "Accept: application/vnd.github.raw+json" | Out-String)))
```

The installer configures Git credentials through the authenticated GitHub CLI session, resolves `v1.8.1` to a full commit SHA before reading or changing the profile, creates a local recovery copy before any removal, and updates the `cognitive-powers` marketplace with that immutable SHA. It removes other installed copies with the same plugin name, installs `cognitive-powers@cognitive-powers`, and verifies that exactly one entry is enabled at the version the release ref names. If an upgrade step fails, it restores the prior marketplace and previously enabled copies; if complete restoration is impossible, it preserves the recovery marketplace path and fails closed. Restart Codex before starting a new task.

To roll back immutably, run the audited `v1.8.1` installer with the newest earlier tag that was actually published — 1.6.0 and 1.7.0 exist only as changelog sections, never as tags. The same recovery transaction protects the currently installed release:

```powershell
& ([scriptblock]::Create((gh api 'repos/Drizzy07x/cognitive-powers/contents/install.ps1?ref=v1.8.1' -H "Accept: application/vnd.github.raw+json" | Out-String))) -ReleaseRef v1.7.2
```

## Update the local development installation

The release and local-development routes are mutually exclusive: only one enabled installation of `cognitive-powers` may exist. To switch to the local checkout after its source version passes validation, remove the release-marketplace installation first:

```powershell
codex plugin remove cognitive-powers@cognitive-powers --json
codex plugin add cognitive-powers@personal --json
codex plugin list --json
```

The installed entry must report the same version as `.codex-plugin/plugin.json`, and `codex plugin list --json` must show exactly one installed and enabled entry named `cognitive-powers`. Running the GitHub installer later removes the local-development entry before installing the tagged release.

## Install in Claude Code

The same tree is also a Claude Code plugin. Nothing is shared at runtime between
the two hosts: Codex reads `.codex-plugin/plugin.json` and `skills-core/`, while
Claude Code reads `.claude-plugin/plugin.json` and `skills/`. Both are validated
from one source and a drift gate keeps their versions identical.

Add the marketplace and install from a local checkout:

```text
/plugin marketplace add <path-to-checkout>
/plugin install cognitive-powers@cognitive-powers
```

Alternatively, place the checkout at `~/.claude/skills/cognitive-powers/`. Any
directory under a skills directory that carries `.claude-plugin/plugin.json`
loads as `cognitive-powers@skills-dir` on the next session with no marketplace
and no install step.

Claude Code prompts once for **Python 3 executable** at enable time. This value
is required and has no default because no interpreter name resolves correctly on
every platform. On Windows, `python3` resolves to the Microsoft Store alias in
`WindowsApps`, which exits without running Python; point the setting at the real
`python.exe` instead. Confirm the choice before entering it:

```powershell
& <path-to-python> --version
```

Hooks are invoked in exec form, so this path is passed as an argument vector and
is never expanded by a shell.

Claude may load any of the nineteen workflows when its trigger contract matches,
and every one of them stays directly invocable as `/solve-efficiently`,
`/map-project`, `/verify-web-behavior`, and so on. Only the listing text, a
description plus `when_to_use` per skill, is held in context; a workflow body
loads when it is used.

The plugin's three agent roles register under plugin-scoped names such as
`cognitive-powers:verifier`. None of them is granted `Agent`, so a worker cannot
spawn its own workers and depth one is a property of the tool set rather than a
request. The verifier withholds the edit tools and runs under `isolation:
worktree`: it keeps `Bash`, because verification means running real checks, and
`Bash` is exactly what a withheld-edit-tools list does not contain -- the
disposable checkout is what makes the read-only claim true of your tree.

The plugin also declares one MCP server, `cognitive-powers-evidence`, so a host
that cannot shell out can still read the durable store: `inspect_evidence_storage`,
`summarize_durable_session`, and `check_durable_session_schema`. Every published
tool is an inspection. Mutation stays on `work_state.py`, where the ownership,
lock, and independent-verifier gates live, and the server does not reimplement
the read path either -- each tool runs the canonical subcommand and returns its
JSON, because two implementations of one contract diverge and the one behind an
MCP boundary is the one nobody would notice diverging. The tool table is the
allowlist: a name that is not in it reaches no subprocess, so no argument a
caller supplies can select a mutating subcommand.

To find out whether an installed copy actually runs on your host rather than
merely being packaged correctly, invoke `/verify-installation`. It executes the
interpreter, both hooks, the shared evidence root, and a durable receipt round
trip, reports optional providers honestly, and asks the model to add the two
facts no script can see from outside the host: which of these skills appear in
its own listing, and whether the agent roles registered. `scripts/doctor.py`
remains the packaging diagnostic; it describes declarations on disk and never
claims anything ran.

A `SessionStart` hook refreshes the optional semantic index, so navigation
reads a graph that matches the worktree. It acts only when Graphify is already
installed, the working directory is a checkout, and an index is already there:
it never installs Graphify and never creates an index, so a repository you do
not own is left untouched. It also skips the rebuild when Git reports the
worktree unchanged since the last run. Set `COGNITIVE_POWERS_DISABLE_INDEX=1`
to turn it off, or `COGNITIVE_POWERS_INDEX_TIMEOUT` to change its bound.

Verify an explicitly authorized installed copy against the immutable tag:

```powershell
& $python scripts/verify_installed.py --source-root . `
  --installed-root <isolated-installed-root> --tag v1.8.1 --host claude-code
```

The `claude-code` host verifies tagged content and packaging only. It never
reads the host's installation registry and reports `hostInventoryVerified` as
false, so it is not a complete installed-host verification. Structural
packaging is additionally checked by `claude plugin validate . --strict` in CI
and by `tests/test_claude_plugin_contract.py` offline.

Durable evidence still defaults to `~/.codex/cognitive-powers` on both hosts.
The name is historical; the location is shared deliberately so a machine running
both hosts keeps one durable state. Override it with `COGNITIVE_POWERS_DATA`.

## Doctor

Doctor is read-only. It reports the plugin name, version and root; Python runtime; skills; hook configuration; Git and source identity; declared optional providers; and whether the validation entrypoints are present. It does not search for provider executables, access the network, read provider credentials, install anything, or claim live validation.

```text
& $python scripts/doctor.py --json
```

To validate the release layout without publishing, package and inspect a disposable temporary copy:

```text
& $python scripts/doctor.py --validate-installation --json
```

The temporary package is deleted after inspection. This validates installation structure only; it is not an installed-host or marketplace test.

For an explicitly authorized installed copy, verify every tagged blob with Git
attribute normalization, the commit-pinned marketplace, exact enabled plugin
inventory, and the three-skill public surface:

```powershell
& $python scripts/verify_installed.py --source-root . `
  --installed-root <isolated-installed-root> --tag v1.8.1
```

Exit codes distinguish identity (`10`), content (`11`), inventory (`12`), and
host CLI (`13`) failures. CI never points this verifier at a real user profile;
its integration gate sets `CODEX_HOME`, `HOME`, and `USERPROFILE` to one
disposable fixture home and parses the JSON report by invariants.

## Durable resume and compaction

The supported durable CLI derives resume data from the verified ledger rather
than free-form text. Compaction first writes and verifies a deterministic bundle
outside the session, then atomically retains a checkpoint and at least one event:

```powershell
& $python skills/execute-durably/scripts/work_state.py --root <workspace> `
  --data-root <external-data-root> resume-summary --session <id> --json
& $python skills/execute-durably/scripts/work_state.py --root <workspace> `
  --data-root <external-data-root> compact --session <id> `
  --bundle <external-bundle.zip> --retain-events 25 --json
```

Completed packets are never returned as runnable. Corrupt ledgers and invalid
bundle boundaries fail closed; compaction never deletes the last verifiable
state or performs a destructive schema downgrade.

## Release and compatibility evidence

`scripts/build_release_manifest.py` accepts only an exact single tag at its
commit and binds the archive digest, every tracked file, CI OS/Python axes, and
the public skills/hooks surface. Tag CI rebuilds twice and compares manifests
and archive bytes before preserving candidate assets; it does not publish a
release. `compatibility-baseline.json` and `docs/compatibility.md` are generated
only from validated CI receipts. The in-repo baseline is empty by construction:
it holds all 108 combinations as `unknown`, because a receipt bound to a CI run
cannot be produced locally, and an unbacked cell is never assumed to work.

The populated matrix is a release asset. For v1.8.1, `compatibility-matrix.json`
reports all 108 cells `compatible`, each from a receipt whose commit, run ID, and
run attempt are verified against the run that produced it before the assets are
preserved.

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

“Offline” means deterministic local contract or fixture validation. It does not mean the associated external provider or user-visible workflow was exercised.

## Evaluation protocol

Claims about the adaptive controller require paired runs of the same Cognitive Powers build with `controller_mode=forced-solo` and `controller_mode=adaptive`; Codex base is an exploratory comparison, not the causal control. The versioned protocol freezes model, reasoning effort, prompt, tools, permissions, slots, fixture and Git identity, task version, evaluator identities, and balanced arm order. It uses four expected modes across five categories: bug fixing, multi-file implementation, current-source research, delivery verification, and real-host interaction.

Protocol v18 freezes the persistent parent-thread host identity, explicitly enables native multi-agent support, and binds one immutable complete plan to persistent parent/child rollouts and their observed usage. The evaluated runtime receives opaque fixture IDs and no benchmark manifest, expected mode, category, split, hidden check, or evaluator route. Read-only assignments may declare path ownership as a bounded read scope, but their pre-evaluator diff must remain empty. `selected_mode` is a planning decision; `executed_mode` is populated only from host-backed execution evidence. A missing thread, unknown rollout schema, missing descendant usage, unrelated child, declared-but-unobserved agent, unbound lifecycle identifier, replacement plan, non-canonical plan, or evaluation-label leak invalidates the experiment. Protocols v1-v17 and their incomplete or invalid preflights are historical evidence only and are not reusable for v18 claims.

- Pilot: 20 unique fixtures, three repetitions per arm, for 120 provider sessions.
- Promotion: 60 new held-out fixtures, three repetitions per arm, for 360 provider sessions.
- Score correctness and independent tests before efficiency; report every failure and reject critical failures.
- Compare token efficiency only among paired successful runs. Keep input, cached input, fresh input, output, and total tokens separate.
- A combined “better quality and fewer tokens” claim requires zero critical failures, non-inferior success, mean quality at least five points higher with a paired 95% confidence interval excluding zero, median total tokens at least 15% lower, median fresh input at least 20% lower, and at most 5% overhead on `solo` tasks. Token ratios use successful pairs only and must pass their fixture-level bootstrap confidence bounds.

`benchmarks/evaluation_tasks.json` contains 80 distinct task definitions and the frozen 20/60 schedule, not run results. Repeated executions of one fixture do not count as independent fixtures. `controller_ab_fixtures.py` materializes the actor checkouts and evaluator-only checks outside the plugin repository, initializes each actor checkout as a clean Git repository, and refuses a ready status until all identities and seals match. The offline integration fixture keeps end-to-end improvement false.

`benchmarks/controller_ab_protocol.json` freezes the controller-specific design,
promotion gates, required artifacts, and current `not-proven` state. It contains
neither fixture definitions nor provider results; those must be supplied and
hashed before a live run.

Prepare the private fixture bundle and validate it before any provider call:

```powershell
& $python scripts/controller_ab_fixtures.py validate
& $python scripts/controller_ab_fixtures.py materialize --round pilot --output-root <empty-absolute-private-pilot-root>
& $python scripts/controller_ab_fixtures.py validate --round pilot --materialized-root <private-pilot-root>
```

After committing a clean experimental source snapshot, create two minimal and
byte-equivalent authenticated homes, generate the private batch configuration,
and run the resumable schedule:

```powershell
& $python scripts/prepare_controller_ab_homes.py --source-home <codex-home> --plugin-source . --output-root <private-homes-root> --model <model> --reasoning-effort <effort>
& $python scripts/controller_ab_fixtures.py write-batch-config --materialized-root <private-pilot-root> --task-contract benchmarks/evaluation_tasks.json --controller-protocol benchmarks/controller_ab_protocol.json --baseline-home <baseline-home> --candidate-home <candidate-home> --model <model> --reasoning-effort <effort> --round pilot --output <private-pilot-config.json>
& $python scripts/controller_ab_batch.py --config <private-pilot-config.json> --output <private-preflight-evidence-root> --preflight
& $python scripts/controller_ab_batch.py --config <private-pilot-config.json> --output <private-pilot-evidence-root>
& $python scripts/controller_ab_fixtures.py materialize --round promotion --output-root <empty-absolute-private-promotion-root>
& $python scripts/controller_ab_fixtures.py validate --round promotion --materialized-root <private-promotion-root>
& $python scripts/controller_ab_fixtures.py write-batch-config --materialized-root <private-promotion-root> --task-contract benchmarks/evaluation_tasks.json --controller-protocol benchmarks/controller_ab_protocol.json --baseline-home <baseline-home> --candidate-home <candidate-home> --model <model> --reasoning-effort <effort> --round promotion --output <private-promotion-config.json>
& $python scripts/controller_ab_batch.py --config <private-promotion-config.json> --output <private-promotion-evidence-root>
& $python scripts/finalize_controller_ab_evidence.py --coordinator-output <private-pilot-evidence-root> --coordinator-output <private-promotion-evidence-root> --bundle-output <private-confirmatory-bundle-root> --verifier-receipt <host-independent-verifier-receipt.json> --task-contract benchmarks/evaluation_tasks.json --controller-protocol benchmarks/controller_ab_protocol.json
& $python scripts/integration_evaluation.py --receipts <private-confirmatory-bundle-root>/session-receipts.jsonl --tasks benchmarks/evaluation_tasks.json --controller-protocol benchmarks/controller_ab_protocol.json --artifact-index <private-confirmatory-bundle-root>/sha256-index.json
```

The pilot and promotion use physically distinct roots and batch configurations.
Each schema-v3 batch configuration freezes the absolute canonical plugin source;
the coordinator re-reads its clean Git identity and the runner compares both
installed caches against that source instead of trusting Codex's reported
source path.
On Windows, use a short external `--work-root`. If the host downgrades
`workspace-write` to read-only, the mutation preflight is invalid; only an
explicitly authorized, newly frozen `--bypass-sandbox` configuration may retry
it.
Verify that preflight completed before starting the scored pilot. Do not
materialize the promotion root until the pilot has passed its blinded
instrumental gate; this keeps held-out fixtures unavailable to pilot execution
and debugging. The independent verifier receipt must declare host provenance,
an experiment-verifier identity distinct from every experiment runner, a
confirmed independent verdict, and the exact sorted SHA-256 values of both
coordinator indexes. The finalizer validates and preserves both source roots,
then creates a separate deterministic combined bundle. The batch journal never
automatically retries an interrupted provider job; the operator must inspect it
first to avoid duplicate calls. Full receipts and events remain private, while
a public report may contain only sanitized metrics and their immutable hashes.

## Live evidence limitations

The default suite is offline and does not validate current providers, credits, browsers, desktop applications, or public host behavior. A live claim requires an explicitly authorized command, current objective-level evidence, artifact hashes, and independent review. Doctor never upgrades availability into validation.

No product screenshots are claimed because Cognitive Powers currently has no verified public host surface suitable for a real capture. The manifest intentionally keeps `screenshots` empty; screenshots will be added only after the public surface and capture are verified.

## Validate

Set `$python` to a working Python 3 executable. In Codex desktop, use the Python path returned by the workspace dependency loader rather than the Microsoft Store alias.

Install the exact validation dependency set before invoking the canonical entrypoint:

```powershell
& $python -m pip install --require-hashes -r requirements-dev.txt
```

Run the complete declared offline surface with one command. The JSON receipt must be outside the plugin root so it does not dirty or alter the source identity being validated:

```text
& $python scripts/validate_all.py --offline --json-output <outside-repo-validation.json>
```

The command fails closed when Git has no real HEAD, the worktree is dirty, source identity changes during execution, a declared command is missing, or any real exit code is nonzero. It records offline and live status separately and never runs live checks by default.

GitHub Actions treats the blocking `validate_all.py` result as the code check and
reports receipt publication separately. The workflow always prints the receipt
SHA-256, bound Git/source identities, and failed-command tails. Artifact upload is
non-blocking for the PR check because repository quota is external to code
validation, but it is never relabeled as successful: `receipt_uploaded=false`
blocks release preparation. A green validation check does not mean release-ready;
that claim still requires preserving the receipt through a valid channel and
creating an independent release witness.

The canonical offline surface executed by that entrypoint is listed below and checked against the orchestrator by tests:

```powershell
& $python --version
& $python scripts/validate_skills.py
& $python scripts/validate_skills.py --strict-quality
& $python -m unittest tests.test_live_ab_runner tests.test_controller_ab_protocol tests.test_controller_ab_fixtures tests.test_controller_ab_batch tests.test_controller_ab_evidence
& $python -m unittest discover -s tests -v
& $python -m ruff check .
& $python -m ruff format --check .
& $python scripts/run_benchmarks.py
& $python scripts/run_durability_benchmarks.py
& $python scripts/run_communication_benchmarks.py
& $python scripts/run_design_benchmarks.py
& $python scripts/run_capability_benchmarks.py
& $python scripts/run_coordination_benchmarks.py
& $python scripts/controller_ab_fixtures.py validate
& $python scripts/run_qcu_benchmarks.py
& $python scripts/run_skyvern_benchmarks.py
& $python scripts/run_extension_benchmarks.py
& $python scripts/run_skill_routing_benchmarks.py
& $python scripts/run_memory_benchmarks.py --json
& $python scripts/external_catalog.py validate
& $python scripts/integration_adapters.py all
& $python scripts/integration_evaluation.py --receipts benchmarks/integration_evaluation_cases.json
& $python skills/execute-durably/scripts/work_state_core/mutation_probe.py --root .
& $python tests/fixtures/run_verify_installed_fixture.py
& $python scripts/build_compatibility_matrix.py --contract compatibility-contract.json --json-output compatibility-baseline.json --markdown-output docs/compatibility.md --check
& $python scripts/doctor.py --validate-installation --json
```

The integration-evaluation fixture is offline and keeps `end_to_end_improvement_proven` false. Replace it with current provider-backed paired receipts before making an improvement claim.

External source decisions and immutable revisions live in `integrations/catalog.json`. Discovery catalogs never install skills; candidates must pass capability, provenance, routing, and behavioral gates first. `integration_adapters.py` only detects existing providers unless `--execute` explicitly authorizes a bounded version probe.

To validate semantic navigation against an already indexed fixture or repository:

```powershell
& $python scripts/run_semantic_benchmarks.py --codegraph <codegraph-executable> --fixture-root <indexed-fixture>
```

This suite intentionally fails when it receives lexical fallback results instead of real CodeGraph output.

To validate browser evidence against a fixture copy with Playwright and its selected browser already installed:

```powershell
& $python scripts/run_browser_benchmarks.py --playwright <playwright-executable> --fixture-root <installed-fixture>
```

This suite intentionally fails when Playwright is absent, no expected test executes, an unexpected result occurs, or required evidence artifacts are missing.

Validate the offline QCU transcript and evidence contract:

```powershell
& $python scripts/run_qcu_benchmarks.py
```

This suite validates normalization and fail-closed behavior only and reports `liveDesktopValidated` as false. A real desktop claim requires an authorized QCU live run, current raw outputs, objective-level verification, and independent review.

Validate the offline Skyvern normalization and handoff contract:

```powershell
& $python scripts/run_skyvern_benchmarks.py
```

The report keeps `liveProviderValidated` false unless an authorized live observation is explicitly supplied with `--live-url` and `--live-prompt`. Live Skyvern execution may consume provider credits and is never started by the default validation suite.

Validate adaptive profile selection and evidence-preserving output contracts:

```powershell
& $python scripts/run_communication_benchmarks.py
```

This offline suite keeps `endToEndImprovementValidated` false. A real efficiency claim requires paired successful provider runs, equal-or-better quality, zero critical failures, and the thresholds in the delivery evaluation protocol.

Validate design-intent inference and abstention contracts:

```powershell
& $python scripts/run_design_benchmarks.py
```

This suite keeps both `liveRenderValidated` and `visualQualityValidated` false. Real visual evidence requires current Playwright results, dimension-matched mobile and desktop PNG renders, and an explicit reviewer contract.

Validate recurring-work classification, duplicate-event handling, stale-memory rejection, and update-before-new routing:

```powershell
& $python scripts/run_capability_benchmarks.py
```

This offline suite keeps `quality_improvement_proven` false. It validates the audit contract and expected classifications, not an end-to-end improvement in model quality.

Validate adaptive diagnostic routing, review-angle selection, and work-packet planning:

```powershell
& $python scripts/run_coordination_benchmarks.py
```

This offline suite keeps `end_to_end_improvement_proven` false. It validates routing and state contracts, not a measured improvement in model outcomes.

Validate compiled plans, portable prompt contracts, selective hook configuration, and bounded frontend review:

```powershell
& $python scripts/run_extension_benchmarks.py
```

This offline suite keeps `liveCodexHookValidated`, `runtimePerformanceMeasured`, `semanticPromptQualityProven`, and `endToEndImprovementProven` false. It executes the hook script with a synthetic Codex-shaped payload and validates deterministic contracts; a real Codex hook session, semantic prompt review, and measured browser or bundle evidence remain separate requirements.

Durable session data is stored outside the target repository under `COGNITIVE_POWERS_DATA`, `PLUGIN_DATA`, or `~/.codex/cognitive-powers` in that order. An override that resolves inside the repository is rejected before state is created.

## License

Released under the [MIT License](LICENSE).

Third-party components and referenced projects remain subject to their own
licenses. Every audited source, its observed license, and its adoption decision
are recorded in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and
`integrations/catalog.json`. No third-party source is vendored: the adapters for
Playwright, Skyvern, and QCU are original implementations, and nothing under
`ci/` beyond `package.json` and `package-lock.json` is tracked.
