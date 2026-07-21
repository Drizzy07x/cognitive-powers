<p align="center">
  <img src="assets/logo.png" alt="Cognitive Powers" width="720">
</p>

# Cognitive Powers

Cognitive Powers is a Codex plugin for executing non-trivial work with focused context and evidence-based verification.

## Quickstart: three flows

Use the smallest flow that fits the work:

1. **Focused solve** — ask Codex to use `$solve-efficiently` for a bounded implementation, diagnosis, or research task. It selects only the context and checks justified by the request.
2. **Durable execution** — ask Codex to use `$execute-durably` when work spans several steps, agents, or compactions. Criteria, command exits, source fingerprints, and independent verification remain external to the target repository.
3. **Delivery verification** — ask Codex to use `$verify-delivery` with the original claim and relevant checkout. It reports Contract and Quality separately and does not turn missing or stale evidence into success.

These are prompt-level plugin flows; they do not install optional providers or authorize publication, live browser actions, or desktop input.

Version 1.3 provides:

- A typed context pipeline with ordered providers and processors, strict character budgets, per-item inclusion receipts, and deterministic lint for duplicates, contradictions, expiry, and unused context.
- A routing benchmark for every installed skill with positive, negative-owner, adversarial-pressure, rank-1, top-k, and collision cases without claiming end-to-end model improvement.
- Provider-neutral semantic navigation across an existing fresh CodeGraph index, an existing fresh Graphify export, or bounded lexical fallback.
- Demand-only, project-scoped memory through native JSON or SQLite stores and an optional existing memU CLI, with source hashes, timestamps, confidence, expiry, supersession, snapshots, and guarded undo.
- An evidence-bound capability lifecycle: `observed`, `candidate`, `trial`, `active`, and `retired`, with distinct events, immutable checks, approval, implementation fingerprints, and rollback evidence.
- A reproducible research skill with frozen pre-registration, confirmatory/exploratory separation, claim-to-evidence binding, dead ends, pivots, and an independent verdict.
- Optional knowledge closeout across code, runtime, documentation, rules, memory, and workspace without implicit cleanup or memory writes.
- A versioned external capability catalog covering all audited sources with immutable SHAs, licenses, decisions, states, labels, and compare-and-swap updates.
- Passive adapters for Context Mode, Graphify, memU, Ruflo, Nacos, LobeHub exchange, and Obsidian that never install providers and always retain a native fallback.
- Durable coordination reports, a hash-bound release witness, and a paired evaluation gate that refuses unsupported improvement claims.

- Adaptive routing that avoids heavy process for simple requests.
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
- Durable external work state with atomic writes, an append-only ledger, source fingerprints, and resumable criteria.
- Dependency-aware work packets with exclusive path ownership, immutable argv checks, owned-path fingerprints, and an integration gate that remains separate from completion.
- A strict Markdown-to-work-packet compiler that rejects incomplete sections, shell-string checks, unsafe paths, ownership overlap, unknown dependencies, and cycles before state changes.
- Narrow executor, test-writer, and read-only verifier role contracts without fixed model versions; repository TOML agents improve development while installed-plugin workflows retain the same roles through skill instructions.
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

The current tests validate plugin structure, context selection, state transitions, real exit-code capture, evidence integrity, and completion gates. They do not yet prove an end-to-end improvement in model quality or token usage.

Context7, CodeGraph, Playwright, QCU, and Skyvern are optional. Cognitive Powers continues to work without these integrations. It never installs or initializes them inside a target repository implicitly. QCU is never started merely because its skill loads. Skyvern completion, QCU primitive success, graph-selected tests, screenshots, traces, recordings, and generated candidates remain supporting evidence until a relevant objective-level assertion passes.

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

Claims that Cognitive Powers improves Codex require paired baseline-versus-candidate runs, not manifest checks or synthetic benchmark scores. The versioned protocol freezes model, reasoning effort, prompt, tools, permissions, fixture, source identity, task version, and balanced arm order. It uses five categories: bug fixing, multi-file implementation, current-source research, delivery verification, and real-host interaction.

- Pilot: 20 paired runs across the five categories.
- Promotion: 50 held-out paired runs across disjoint task fixtures.
- Score correctness and independent tests before efficiency; report every failure and reject critical failures.
- Compare token efficiency only among paired successful runs. Keep input, cached input, fresh input, output, and total tokens separate.
- A combined “better quality and fewer tokens” claim requires zero critical failures, success no lower than baseline, mean quality at least five points higher, median total tokens at least 15% lower, median fresh input at least 20% lower, and at most 5% overhead on tasks where the plugin should abstain.

`benchmarks/evaluation_tasks.json` contains frozen task definitions and schedules, not run results. The offline integration fixture keeps end-to-end improvement false.

## Live evidence limitations

The default suite is offline and does not validate current providers, credits, browsers, desktop applications, or public host behavior. A live claim requires an explicitly authorized command, current objective-level evidence, artifact hashes, and independent review. Doctor never upgrades availability into validation.

No product screenshots are claimed because Cognitive Powers currently has no verified public host surface suitable for a real capture. The manifest intentionally keeps `screenshots` empty; screenshots will be added only after the public surface and capture are verified.

## Validate

Set `$python` to a working Python 3 executable. In Codex desktop, use the Python path returned by the workspace dependency loader rather than the Microsoft Store alias.

Run the complete declared offline surface with one command. The JSON receipt must be outside the plugin root so it does not dirty or alter the source identity being validated:

```text
& $python scripts/validate_all.py --offline --json-output <outside-repo-validation.json>
```

The command fails closed when Git has no real HEAD, the worktree is dirty, source identity changes during execution, a declared command is missing, or any real exit code is nonzero. It records offline and live status separately and never runs live checks by default.

The canonical offline surface executed by that entrypoint is listed below and checked against the orchestrator by tests:

```powershell
& $python --version
& $python scripts/validate_skills.py
& $python scripts/validate_skills.py --strict-quality
& $python -m unittest discover -s tests -v
& $python scripts/run_benchmarks.py
& $python scripts/run_communication_benchmarks.py
& $python scripts/run_design_benchmarks.py
& $python scripts/run_capability_benchmarks.py
& $python scripts/run_coordination_benchmarks.py
& $python scripts/run_qcu_benchmarks.py
& $python scripts/run_skyvern_benchmarks.py
& $python scripts/run_extension_benchmarks.py
& $python scripts/run_skill_routing_benchmarks.py
& $python scripts/run_memory_benchmarks.py --json
& $python scripts/external_catalog.py validate
& $python scripts/integration_adapters.py all
& $python scripts/integration_evaluation.py --receipts benchmarks/integration_evaluation_cases.json
& $python skills/execute-durably/scripts/work_state_core/mutation_probe.py --root .
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
