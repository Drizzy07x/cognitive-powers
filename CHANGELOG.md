# Changelog

## 1.7.0 - 2026-07-26

- Added a second host surface for Claude Code from the same source tree: a `.claude-plugin/plugin.json` manifest, a marketplace entry, plugin-root agent definitions, and a hook configuration that keeps the existing Codex packaging unchanged.
- Declared the hook interpreter as a required `userConfig` file value and invoked it through exec form, because no interpreter name resolves correctly on every platform; on Windows the `python3` alias resolves to a Microsoft Store stub that exits without running Python.
- Exposed all fourteen workflows to automatic model invocation on Claude Code. `disable-model-invocation` removes a skill from the listing the model sees, so the eleven specialized workflows were unreachable to it while the core workflows still instructed it to invoke them by name; that guidance could not be followed. Each description now carries an explicit `when_to_use` trigger contract, and both the routing benchmark and the description quality gate score the description and trigger together, matching the single listing entry the host actually shows. Deterministic routing accuracy is unchanged.
- Made `map-project` resolve the instruction filename the running host reads. Claude Code reads `CLAUDE.md` and never `AGENTS.md`, so the workflow previously produced project memory that host would not load. When the other host's file already exists it is imported rather than duplicated.
- Corrected the durable agent-role reference, which stated that installed plugins do not register custom agents. Claude Code does register an enabled plugin's `agents/` directory, so the roles are available there under plugin-scoped names, and the verifier's read-only tool grant is enforced by the host rather than only described.
- Stopped the selective hook from reading a host-specific data variable. Claude Code exports `CLAUDE_PLUGIN_DATA` to hook processes only, so the hook resolved a data root that `work_state.py` could never reach and the `Stop` gate rejected receipts for work that was genuinely complete. Both now resolve one root on every host, gated by a test.
- Delivered the `Stop` evidence warning to the agent as well as the user on Claude Code. `systemMessage` is shown only to the user there, so the warning named a gap the one party able to close it could not read. The added context is advisory and the hook stays fail-open.
- Extended capability auditing to the Claude Code skill locations, `.claude/skills` and `.claude/commands`, so an already-installed capability is not proposed again.
- Repaired Graphify semantic navigation, which had never worked against a real installation. The completeness detector required `graphify-out/.graphify_python` pointing at an interpreter that can `import graphify`; nothing writes that marker, and Graphify also ships as a self-contained executable that exposes no importable package, so every index was reported incomplete and navigation always fell back to lexical search. Only a test fixture had ever produced the marker. The provider's own detector stays the fast path where it is genuinely available, and a manifest-based detector now decides the remaining question exactly. The reported `detector` names which one answered, and `new_file_detection` states the manifest detector's known gap rather than guessing at Graphify's corpus rules, which produced false incomplete verdicts.
- Added `verify-installation`, a workflow that establishes whether an installed copy actually runs on the current host. Every defect fixed in this release passed structural validation and failed only when executed, so `scripts/selfcheck.py` runs things instead of reading them: the interpreter, both hooks against synthetic host payloads, agreement on one evidence root, and a durable receipt round trip, with optional providers reported as available or absent rather than as failures. Writes happen only in temporary directories. The skill also asks the model to report the two facts no script beside the host can observe: which of these skills appear in its own listing, and whether the agent roles registered. `doctor.py` remains the packaging diagnostic and still never claims anything ran.
- Sharpened `execute-durably`'s description, which had drifted onto `verify-delivery`'s ground by naming completion claims and auditable evidence. Adding a fifteenth skill shifted the corpus weighting enough for it to win an adversarial routing case it should lose. Deterministic routing now scores better than the fourteen-skill surface: rank-1 0.9583, top-k 1.0, negative-owner 1.0, adversarial-owner 1.0, with no failing case.
- Refused a session name that only collides with a stored one. `sanitize_identifier` is deliberately lossy, so `release/alpha`, `release alpha`, and `release:alpha` all reduce to `release-alpha`, as do any two names sharing an 80-character prefix. `init` already refused to overwrite, but every other subcommand silently read and would then have mutated the other session's state, which defeats durable resumption. The caller's name is now stored at initialization and checked whenever a session is opened; sessions written before the field existed stay readable.
- Closed a bypass of the independent-verification guard. `sanitize_identifier` substituted characters outside `[A-Za-z0-9._-]` without composing first, so one logical name produced two identities depending on how it was typed: composed `é` is a single unmapped codepoint and vanished, while decomposed `é` kept its `e` and lost only the combining mark, turning `agent-café` into `agent-caf` or `agent-cafe`. Executor and verifier identities are compared to refuse self-verification, so a single actor could present each form, confirm its own claim, and close the session; this was reproduced end to end, with `complete` exiting 0. Identities are now composed before substitution, and the same actor is refused in either form.
- Closed a Unicode hole in parallel write ownership. The overlap check folded case but not composition, so two owners claiming the same file as `café.py` in composed and decomposed form were judged not to overlap and could be scheduled into the same wave. macOS resolves both spellings to one file. Owned paths are now composed before comparison in the planner, the durable state machine, and the packet compiler.
- Closed a fail-closed breach in the orchestrator. `select_agent_plan` promises that a rejected input degrades to a solo plan, but it caught only `OrchestrationError`, and an unhashable value in `schema_version`, `request_mode`, `phase`, or `authorization` reached `value in some_set` and raised `TypeError` instead. A fuzz over 80,000 malformed payloads produced 8,668 crashes; it now produces none. Enum membership rejects rather than raises, a backstop keeps the guarantee from depending on enumerating call sites, and the CLI reports the documented error shape instead of a traceback. The same check also stops Python treating `True` as schema version 1. `create_receipt` required `usage.cached_input_tokens`, a field only the Codex rollout parser produces, so a native Anthropic usage record was rejected outright and `record-communication` could never accept one. Both shapes are now read, and the reported `usage.sourceSchema` names which. The conversion is not a rename: Anthropic counts `input_tokens` as uncached input only and states the cached prefix separately, so total input is the sum of the three input fields, cache writes count as fresh, and a cache read routinely exceeds the uncached remainder, which the Codex guard treats as invalid. `compare` now refuses receipts from different schemas, whose totals are not like for like.
- Applied the same conversion in the durable recorder, which re-derives receipt totals from the raw provider record rather than trusting them. It read only the Codex field names, so it rejected a correct Anthropic receipt. Both sides now call one implementation in `scripts/provider_usage.py`: reading fewer provider shapes than the receipt writer accepts rejects correct evidence, which is a failure worth removing rather than detecting after the fact.
- Added `usage-from-transcript`, which builds an Anthropic usage record from a Claude Code transcript, the analogue of the Codex rollout parser. Claude Code writes one transcript row per content block and repeats the identical usage on each, so usage is counted once per message id; summing rows would multiply a single message's cost. It refuses a transcript covering more than one model and reports how many messages it counted. The transcript is not a published interface, so its shape may change; the producer refuses outright when a message's usage is not fully readable, because a partially recognised row would undercount and an undercount reads as a genuine efficiency result, and it records the Claude Code build in `hostVersions` so a later discrepancy identifies the format that produced the numbers.
- Added a `SessionStart` hook on Claude Code that runs `graphify update` for a checkout when Graphify is already installed, so an existing index tracks the worktree instead of going stale. It never installs the provider, skips compaction and fork sources where nothing on disk moved, skips a working directory that is not a checkout, is bounded by an explicit timeout, and always exits zero. It lives in its own `hooks/semantic_index.py` so an index fault cannot reach the evidence recording the completion gate depends on. `COGNITIVE_POWERS_DISABLE_INDEX=1` turns it off. The refresh writes `graphify-out/` into the checkout, so ignore rules should cover it before working in a repository you do not own.
- Replaced host-specific `$skill` invocation syntax in every skill body with host-neutral references, and anchored `scripts/` and `<plugin-root>/` path resolution to the installed manifest so both hosts resolve them identically.
- Made source identity platform-independent. It previously hashed raw worktree bytes, so a checkout made with `core.autocrlf=true` produced a different digest than one made without it, and the same commit did not have one identity across the CI matrix. Text content is now folded to LF before hashing, binary content is still hashed exactly, and the reported `algorithm` names the scheme so digests from different schemes are rejected instead of being read as a content change.
- Added `.gitattributes` so every checkout is LF and binary assets are never translated, and gated both the attribute file and the declared binary suffixes with tests.
- Derived the declared version from the changelog and added fail-closed drift gates across both manifests, the marketplace entry, and the documented install references.
- Durable evidence still defaults to the legacy `~/.codex/cognitive-powers` data root on both hosts. This remains a naming inconsistency under Claude Code, not a functional one. It is now deliberate rather than incidental: the hook and the receipt writer run in different processes, and a single shared root is what lets the `Stop` gate match a receipt to the edit it covers. Set `COGNITIVE_POWERS_DATA` to relocate it.
- No provider-backed evaluation was run, and no end-to-end quality or token claim changed. Claude Code compatibility is validated structurally against the documented plugin schema, by the official `claude plugin validate --strict` check in CI, and by exercising the hook surface against real Claude Code `PostToolUse` and `Stop` payloads. The plugin has still never been loaded by an installed Claude Code host, so no live installed-host claim is made.

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
