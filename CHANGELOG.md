# Changelog

## 1.7.0 - 2026-07-26

- Added a second host surface for Claude Code from the same source tree: a `.claude-plugin/plugin.json` manifest, a marketplace entry, plugin-root agent definitions, and a hook configuration that keeps the existing Codex packaging unchanged.
- Declared the hook interpreter as a required `userConfig` file value and invoked it through exec form, because no interpreter name resolves correctly on every platform; on Windows the `python3` alias resolves to a Microsoft Store stub that exits without running Python.
- Restricted automatic model invocation to the same three core workflows on both hosts. The eleven specialized workflows stay installed and directly invocable but are no longer auto-loaded, preserving the existing catalog-overhead boundary.
- Replaced host-specific `$skill` invocation syntax in every skill body with host-neutral references, and anchored `scripts/` and `<plugin-root>/` path resolution to the installed manifest so both hosts resolve them identically.
- Derived the declared version from the changelog and added fail-closed drift gates across both manifests, the marketplace entry, and the documented install references.
- Durable evidence still defaults to the legacy `~/.codex/cognitive-powers` data root on both hosts. This is a naming inconsistency under Claude Code, not a functional one; migrating it would move existing durable state and is deliberately not part of this release.
- No provider-backed evaluation was run. Claude Code compatibility is validated structurally against the documented plugin schema and by the official `claude plugin validate --strict` check in CI; no live installed-host claim is made.

## 1.6.0 - 2026-07-26

- Added a canonical fail-closed installed-tree verifier bound to immutable Git tags, Git attribute normalization, host inventory, and the exact three-skill public surface.
- Converted the private installer transaction harness into isolated permanent tests covering preflight, source allowlisting, orphan state, rollback, cleanup failure, recovery preservation, and exact success inventory.
- Upgraded doctor to schema v2 structured findings with read-only durable lock, ledger, state, interrupted-write, provenance, and migration diagnostics.
- Combined authenticated ledger hash chains with bounded checkpoint/delta recovery, fixed-size recovery state, automatic compaction, verified pre-compaction bundles, and 1,000 seeded sequences for each offline recovery state machine.
- Added content-addressed evidence storage, deduplicated materialization, bounded inventory, dry-run-first garbage collection, and shared generated-tree exclusions across source identity and copies.
- Made Graphify probing worktree-bound, structurally validated, incrementally complete, bounded in output, and fail-closed to lexical navigation; generated `graphify-out/` state no longer dirties source or package identity.
- Split durable storage/evidence validation, integration-evaluation contracts, and live-runner navigation/telemetry/quality helpers into load-safe core modules while preserving public CLI help, JSON, exception, and isolated-Python behavior.
- Added deterministic planner explanations and ledger-derived resumption summaries that never reschedule completed work packets.
- Added reproducible tagged archives and release manifests, plus a CI-receipt-derived compatibility matrix that reports every untested combination as unknown.
- No provider-backed evaluation was run and no end-to-end quality, token, host-version, or live-installation compatibility claim changed.

## 1.5.2 - 2026-07-24

- Prevented adaptive-plan resumptions from rescheduling units already listed in `completed_unit_ids`.
- Made private and local Codex installations mutually exclusive, pinned the private marketplace to immutable refs, enforced a fail-closed exact source allowlist and tag preflight, created an automatic pre-mutation recovery copy, restored the prior installation on failure, documented immutable rollback to `v1.5.1`, removed duplicate Cognitive Powers entries, and verified exactly one enabled instance.
- Bound release witnesses and tag CI to the sole exact `v<manifest-version>` tag at `HEAD`.
- Scoped doctor Git cleanliness checks to the diagnosed plugin root so unrelated parent-repository changes cannot contaminate the result.
- Ignored Codex host-generated `.codex-marketplace-install.json` metadata in source/package identity and Git cleanliness reporting without hiding real tracked changes.
- Added the host metadata file to the plugin ignore rules so marketplace upgrades remain clean and reproducible.
- Made `tests` an importable package so the canonical targeted `unittest` command used by `validate_all.py`, CI, and the README resolves the repository's test modules reliably.
- Prevented hook-lock contention and stale-file races from silently dropping edit events by moving hashes outside the critical section and using bounded, owner-safe OS file locks.
- Expanded offline CI to Ubuntu, Windows, and macOS with explicit OS-lock coverage before the canonical validator.
- Added fail-closed durability fault/property tests, a read-only schema-migration doctor, and deterministic offline durability benchmarks without provider or model claims.
- Documented skill selection, operations, and the explicit abstention from local usage counters because no privacy-safe natural persistence seam exists.

## 1.5.1 - 2026-07-22

- Pinned the offline validation dependency and made both CI and the documented local workflow install it explicitly.
- Corrected adaptive planning so worker capacity is reused across dependency-aware waves, verifier capacity is reserved only when it runs, retries are not truncated by first-wave capacity, and eligible work is not silently omitted.
- Hardened v2 worker-result validation with exact command receipts, execution-context binding, explicit no-op semantics, complete verifier coverage, and distinct real actor identities.
- Bound persistent rollout evidence per child across spawn, observable join, result, and provider usage. Incomplete lifecycle telemetry is now invalid instead of being mislabeled as degraded controller behavior.
- No provider-backed pilot or promotion was run, and no quality, success-rate, token-efficiency, or performance claim changed.

## 1.5.0 - 2026-07-22

- Hardened the adaptive-controller evaluation to protocol v3: persistent parent/child rollout evidence, an observable controller treatment, selected-versus-executed mode receipts, semantic lifecycle binding, descendant token accounting, invalid-path artifact indexes, and physically separate pilot/promotion roots with schema-v3 locks. Legacy batch configs are non-confirmatory. Previous provider preflights remain invalid and no performance improvement is claimed.

- Added the 80-fixture controller corpus, deterministic external materializer, promotion seals, and fail-closed corpus validation.
- Added minimal equivalent experiment homes and a resumable 240-pair/480-session batch coordinator with globally randomized pairs and immutable journals.
- Added conservative adaptive agent planning with observable solo, parallel read-only, disjoint packet, and staged verification modes.
- The confirmatory provider-backed promotion is incomplete. This release makes no causal quality, success-rate, or token-efficiency claim.

## 1.4.2 - 2026-07-21

- Reduced passive plugin and exposed-skill context for short and already-bounded tasks.
- Tightened current-source decisions around requirement coverage, claim-level citations, version/date semantics, and explicit unknowns.
- Added an explicit rule against inferring retirement or deprecation schedules from unrelated publication or rollout dates.

## 1.4.1 - 2026-07-21

- Added a focused code-and-test fast path that avoids repeated discovery, unchanged-file reads, redundant green checks, and unrelated cleanup.
- Stopped loading `communicate-efficiently` for routine progress and completion handoffs.
- Recorded a historical three-pair live coding pilot against 1.4.0. Its measurements are not reproducible from this checkout without the original immutable provider receipts and therefore are not claim-eligible.
- Reduced the host-visible catalog from fourteen specialized entries to three core workflows while retaining all specialized instructions as demand-loaded internal workflows.
- Added a fail-closed paired live A/B runner with isolated homes and fixture copies, seeded arm order, provider usage receipts, hidden checks, allowed-path enforcement, and guarded-source mutation detection.
- Recorded a historical three-pair bounded-task abstention comparison against Codex base. Its measurements are not used as evidence for the adaptive controller because the receipts are not packaged here and the comparison does not isolate controller behavior.

These measurements cover one bounded coding fixture. Broad end-to-end superiority remains unclaimed until the multi-category pilot and held-out promotion gates pass.

## 1.4.0 - 2026-07-21

- Closed legacy-schema and non-finite-number paths that could create unsupported evaluation claims.
- Made context receipts bijective and budget-bound, rejected consumption of excluded items, and resolved memory supersession before query ranking.
- Tightened optional-provider and skill-routing detection to avoid substring false positives.
- Re-derived release-witness identity and completeness during verification and expanded disposable installation checks.
- Hardened durable locks, source fingerprinting, WAL parsing, and capability transition receipt chains.
- Added supported local Codex update instructions and installed-version verification.

End-to-end quality or token improvement remains unclaimed without eligible paired live executions.

## 1.3.0 - 2026-07-21

- Added a single fail-closed offline validation entrypoint and shared Windows/Linux CI workflow.
- Bound validation and release witnesses to Git and source identity while keeping live checks explicit.
- Added a versioned paired-evaluation contract with pilot and held-out task suites.
- Added focused, standard, and durable orchestration intensity with deterministic routing checks.
- Added opt-in v2 context and memory usage metrics while preserving legacy v1 output by default.
- Recognized Context Mode tool surfaces without installing or executing optional providers.
- Extracted durable storage, locking, fingerprinting, and WAL recovery into `work_state_core`.
- Added fail-closed packet reopening, transitive dependent invalidation, and selective gate mutation probes.
- Added a read-only doctor command, temporary-install validation, compact quickstart, and capability matrix.

End-to-end quality or token improvement is not claimed without eligible paired live executions.
