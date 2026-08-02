# Evidence, validation, and release claims

What each check actually establishes, and what it deliberately does not. The
rule throughout is that absent evidence stays absent: a missing receipt is
reported as `unknown`, never assumed to work.

`docs/compatibility.md` is generated from `compatibility-contract.json` and
re-derived under `--check` in the validation gate, so it is not edited by hand;
change the contract and the table follows. The
[Operational guide](operations.md) is the runbook for everything below that
needs steps rather than context.

## Release and compatibility evidence

Each release ships the evidence for its own claims. The release archive is built
twice and compared byte for byte, and the compatibility matrix carries 108
cells -- three operating systems, two Python versions, and two Codex CLI
versions, each across nine lifecycle scenarios including upgrade, rollback,
CRLF/LF, symlinks, Unicode paths, corrupt state, and a checkout without Git.
Every populated cell comes from a receipt bound to that exact commit and run. A
cell with no receipt stays `unknown` instead of being assumed to work.

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

## Live evidence limitations

The default suite is offline and does not validate current providers, credits,
browsers, desktop applications, or public host behavior. A live claim requires
an explicitly authorized command, current objective-level evidence, artifact
hashes, and independent review. Doctor never upgrades availability into
validation.

No product screenshots are claimed because Cognitive Powers currently has no
verified public host surface suitable for a real capture. The manifest
intentionally keeps `screenshots` empty; screenshots will be added only after the
public surface and capture are verified.

## Run the validation gate

Set `$python` to a working Python 3.11 or newer executable. In Codex desktop,
use the Python path returned by the workspace dependency loader rather than the
Microsoft Store alias.

Install the exact validation dependency set before invoking the canonical
entrypoint:

```powershell
& $python -m pip install --require-hashes -r requirements-dev.txt
```

Run the complete declared offline surface with one command. The JSON receipt
must be outside the plugin root so it does not dirty or alter the source
identity being validated:

```powershell
& $python scripts/validate_all.py --offline --json-output <outside-repo-validation.json>
```

The command fails closed when Git has no real HEAD, the worktree is dirty,
source identity changes during execution, a declared command is missing, or any
real exit code is nonzero. It records offline and live status separately and
never runs live checks by default.

Reading the receipt: `offlinePassed` is whether the declared commands succeeded.
`passed` additionally requires a clean worktree and a stable source identity, so
it is `false` on any dirty tree by design. `skippedTests` is recorded per command
because a skipped assertion is one that did not run.

GitHub Actions treats the blocking `validate_all.py` result as the code check and
reports receipt publication separately. The workflow always prints the receipt
SHA-256, bound Git/source identities, and failed-command tails. Artifact upload is
non-blocking for the PR check because repository quota is external to code
validation, but it is never relabeled as successful: `receipt_uploaded=false`
blocks release preparation. A green validation check does not mean release-ready;
that claim still requires preserving the receipt through a valid channel and
creating an independent release witness.

A green local gate is also not the full gate. CI runs the same commands across
three operating systems, both Python versions, and two lockfile-pinned Codex
CLIs, and the release path is layered on top: manifest reproducibility and
witness on every push, the real install/upgrade/rollback nightly or on dispatch,
the whole path only on a tag.

### The canonical offline surface

The canonical offline surface executed by that entrypoint is listed below and
checked against the orchestrator by tests:

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

The integration-evaluation fixture is offline and keeps
`end_to_end_improvement_proven` false. Replace it with current provider-backed
paired receipts before making an improvement claim.

Durable session data is stored outside the target repository under
`COGNITIVE_POWERS_DATA`, `PLUGIN_DATA`, or `~/.codex/cognitive-powers` in that
order. An override that resolves inside the repository is rejected before state
is created.

## What each optional suite does and does not prove

Two runners are deliberately outside the gate and fail locally without their
provider. To validate semantic navigation against an already indexed fixture or
repository:

```powershell
& $python scripts/run_semantic_benchmarks.py --codegraph <codegraph-executable> --fixture-root <indexed-fixture>
```

This suite intentionally fails when it receives lexical fallback results instead
of real CodeGraph output.

To validate browser evidence against a fixture copy with Playwright and its
selected browser already installed:

```powershell
& $python scripts/run_browser_benchmarks.py --playwright <playwright-executable> --fixture-root <installed-fixture>
```

This suite intentionally fails when Playwright is absent, no expected test
executes, an unexpected result occurs, or required evidence artifacts are
missing.

The suites inside the gate carry explicit ceilings:

| Suite | Keeps false | A real claim additionally requires |
|---|---|---|
| `run_qcu_benchmarks.py` | `liveDesktopValidated` | An authorized QCU live run, current raw outputs, objective-level verification, and independent review |
| `run_skyvern_benchmarks.py` | `liveProviderValidated` | An authorized live observation supplied with `--live-url` and `--live-prompt`; live execution may consume provider credits and is never started by the default suite |
| `run_communication_benchmarks.py` | `endToEndImprovementValidated` | Paired successful provider runs, equal-or-better quality, zero critical failures, and the thresholds in the delivery evaluation protocol |
| `run_design_benchmarks.py` | `liveRenderValidated`, `visualQualityValidated` | Current Playwright results, dimension-matched mobile and desktop PNG renders, and an explicit reviewer contract |
| `run_capability_benchmarks.py` | `quality_improvement_proven` | An end-to-end improvement measurement; the suite validates the audit contract and expected classifications only |
| `run_coordination_benchmarks.py` | `end_to_end_improvement_proven` | A measured improvement in model outcomes; the suite validates routing and state contracts only |
| `run_extension_benchmarks.py` | `liveCodexHookValidated`, `runtimePerformanceMeasured`, `semanticPromptQualityProven`, `endToEndImprovementProven` | A real Codex hook session, semantic prompt review, and measured browser or bundle evidence; the suite executes the hook script with a synthetic Codex-shaped payload and validates deterministic contracts |

## Doctor

Doctor is read-only. It reports the plugin name, version and root; Python
runtime; skills; hook configuration; Git and source identity; declared optional
providers; and whether the validation entrypoints are present. It does not
search for provider executables, access the network, read provider credentials,
install anything, or claim live validation.

```powershell
& $python scripts/doctor.py --json
```

To validate the release layout without publishing, package and inspect a
disposable temporary copy:

```powershell
& $python scripts/doctor.py --validate-installation --json
```

The temporary package is deleted after inspection. This validates installation
structure only; it is not an installed-host or marketplace test.

## Verify an installed copy

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

The `claude-code` host verifies tagged content and packaging only. It never
reads the host's installation registry and reports `hostInventoryVerified` as
false, so it is not a complete installed-host verification. Structural packaging
is additionally checked by `claude plugin validate . --strict` in CI and by
`tests/test_claude_plugin_contract.py` offline:

```powershell
& $python scripts/verify_installed.py --source-root . `
  --installed-root <isolated-installed-root> --tag v1.8.1 --host claude-code
```

To establish that an installed copy actually runs on your host rather than
merely being packaged correctly, invoke `/verify-installation`. It executes the
interpreter, the hooks, the shared evidence root, and a durable receipt round
trip, reports optional providers honestly, and asks the model to add the two
facts no script can see from outside the host: which of these skills appear in
its own listing, and whether the agent roles registered. `scripts/doctor.py`
remains the packaging diagnostic; it describes declarations on disk and never
claims anything ran.

## Evaluation protocol

Claims about the adaptive controller require paired runs of the same Cognitive
Powers build with `controller_mode=forced-solo` and `controller_mode=adaptive`;
Codex base is an exploratory comparison, not the causal control. The versioned
protocol freezes model, reasoning effort, prompt, tools, permissions, slots,
fixture and Git identity, task version, evaluator identities, and balanced arm
order. It uses four expected modes across five categories: bug fixing,
multi-file implementation, current-source research, delivery verification, and
real-host interaction.

Protocol v18 freezes the persistent parent-thread host identity, explicitly
enables native multi-agent support, and binds one immutable complete plan to
persistent parent/child rollouts and their observed usage. The evaluated runtime
receives opaque fixture IDs and no benchmark manifest, expected mode, category,
split, hidden check, or evaluator route. Read-only assignments may declare path
ownership as a bounded read scope, but their pre-evaluator diff must remain
empty. `selected_mode` is a planning decision; `executed_mode` is populated only
from host-backed execution evidence. A missing thread, unknown rollout schema,
missing descendant usage, unrelated child, declared-but-unobserved agent,
unbound lifecycle identifier, replacement plan, non-canonical plan, or
evaluation-label leak invalidates the experiment. Protocols v1-v17 and their
incomplete or invalid preflights are historical evidence only and are not
reusable for v18 claims.

- Pilot: 20 unique fixtures, three repetitions per arm, for 120 provider sessions.
- Promotion: 60 new held-out fixtures, three repetitions per arm, for 360 provider sessions.
- Score correctness and independent tests before efficiency; report every failure and reject critical failures.
- Compare token efficiency only among paired successful runs. Keep input, cached input, fresh input, output, and total tokens separate.
- A combined "better quality and fewer tokens" claim requires zero critical failures, non-inferior success, mean quality at least five points higher with a paired 95% confidence interval excluding zero, median total tokens at least 15% lower, median fresh input at least 20% lower, and at most 5% overhead on `solo` tasks. Token ratios use successful pairs only and must pass their fixture-level bootstrap confidence bounds.

`benchmarks/evaluation_tasks.json` contains 80 distinct task definitions and the
frozen 20/60 schedule, not run results. Repeated executions of one fixture do
not count as independent fixtures. `controller_ab_fixtures.py` materializes the
actor checkouts and evaluator-only checks outside the plugin repository,
initializes each actor checkout as a clean Git repository, and refuses a ready
status until all identities and seals match. The offline integration fixture
keeps end-to-end improvement false.

`benchmarks/controller_ab_protocol.json` freezes the controller-specific design,
promotion gates, required artifacts, and current `not-proven` state. It contains
neither fixture definitions nor provider results; those must be supplied and
hashed before a live run.

### Running the A/B

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

To run the A/B without growing the working tree, follow the procedure in the
[Operational guide](operations.md).
