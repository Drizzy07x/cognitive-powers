# Operational guide

This guide covers local, offline operation. It does not authorize installation,
publication, provider calls, or use of network credits.

## Diagnose the checkout

Doctor is read-only and reports source and Git identity, declared integrations,
the validation surface, and the explicit local usage counters decision:

```powershell
& $python scripts/doctor.py --json
```

`localUsageCounters.status` is `abstained`. The existing natural persistence
seams are edit-event hooks and durable evidence receipts, and both include
context that counters must never retain: prompts, commands, outputs, paths, or identifiers.
Adding counters there would create misleading telemetry or broaden the write
surface. Therefore no counter database and no fake disable, status, reset, or
export controls are implemented.

## Read a source identity

`source.sha256` in a validation receipt identifies a commit's content, not one
checkout of it. Text files are folded to LF before hashing and binary files are
hashed exactly, so Windows, Linux, and macOS agree on the digest for a given
commit regardless of `core.autocrlf`. Binary detection uses Git's own heuristic:
a NUL byte anywhere in the content.

Filenames are composed to NFC before they are ordered or hashed, for the same
reason content is folded to LF. macOS stores names decomposed while Linux and
Windows keep what was written, so a checkout of one commit spells `café.py`
differently per platform; without composing, both the traversal order and the
digest would describe the checkout rather than the commit.

The receipt also carries `source.algorithm`. Digests from different schemes are
not comparable, so a receipt naming another scheme is rejected rather than
reported as a content change. The current value is `sha256-text-normalized-v3`.

Receipts produced before this scheme recorded raw-byte digests and cannot be
compared against current ones. Re-run the canonical entrypoint to produce a
current receipt; do not treat the differing digest as evidence that the source
changed.

## Inspect both host surfaces

The `hosts` section of the doctor report describes the Codex and Claude Code
packaging read from disk. `probed` is always `false`: doctor never executes a
host CLI, so this describes packaging, never a live installation.

Check it after changing a manifest, a hook configuration, or skill frontmatter:

```powershell
& $python scripts/doctor.py --json
```

`versionsAligned` must be `true`. A `host-version-drift` finding means the two
manifests disagree and the release is not coherent. For Claude Code the report
also lists `modelInvocableSkills`, which must list every installed workflow with
none held back, and
`userInvocableOnlySkills`, which must stay empty. The core workflows delegate to
the specialized ones by name, and Claude Code hides a
`disable-model-invocation` skill from the model entirely, so a workflow that
moved into the second list could never be reached by the delegation that names
it. `tests/test_claude_plugin_contract.py` asserts both lists; the cost of the
nineteen descriptions Claude loads on every task is the deliberate price of that
reachability.

`requiredUserConfig` lists values Claude Code prompts for when the plugin is
enabled. `python_executable` has no default because no interpreter name resolves
on every platform: on Windows `python3` resolves to a Microsoft Store alias that
exits without running Python. Verify the configured path with
`<path> --version` before entering it.

The Codex host has no equivalent user-config expansion, so
`hooks/hooks.codex.json` names the interpreter directly: `python3` on POSIX and
the `py` launcher on Windows. Both are hard prerequisites there -- if the spelling does not run,
every Codex hook silently never fires. `doctor.py --validate-installation`
executes that exact spelling as the `codex-hook-interpreter` check and names
the Microsoft Store stub explicitly when it is the culprit. Both hosts now
register the same four events (`SessionStart`, `UserPromptSubmit`,
`PostToolUse`, `Stop`), each exactly once: `clean_code_guard.py` ships but is
not registered, because two entries on one `PostToolUse` matcher started two
interpreters per edit to parse the same stdin twice, and `selective_hooks.py`
calls the guard itself after the ledger event is appended. Shell-driven edits (`sed -i`, heredocs) are
deliberately not matched by the edit-provenance hook on either host, so an
edit made that way carries no provenance record.

Durable evidence defaults to `~/.codex/cognitive-powers` on both hosts. The name
is historical; the shared location is deliberate so one machine running both
hosts keeps a single durable state. Override it with `COGNITIVE_POWERS_DATA`.

## Understand the completion gate

The `Stop` hook warns when no current, hash-bound validation receipt covers the
session's latest recorded edit. Five properties of that gate are read wrongly
often enough to have been filed as defects; each is stated here because the
behaviour is intended.

**A matched edit-tool call arms it, not a file change.** `post_tool_use` appends
one ledger event per matched call, and the gate reads only the latest event.
An `Edit` or `Write` naming a path outside `cwd` records `files: []` --
`_recorded_path` declines to describe anything the hook must not hash -- and that
zero-file event arms the gate exactly like one carrying a digest. The ledger's
job is to record *that* the session edited; a zero-file event is the only trace
left of an edit whose target the hook may not name, so dropping it would leave
none.

**Shell tools do not arm it on either host.** `SUPPORTED_TOOLS` in
`hooks/selective_hooks.py` lists `bash` and the Codex shell spellings so the hook
still behaves on a host that ignores matchers, but no shipped manifest routes a
shell call to it: `hooks/hooks.codex.json` matches
`apply_patch|Edit|Write|MultiEdit|NotebookEdit` and `hooks/hooks.claude.json`
matches the same set without `apply_patch`.
`scripts/run_extension_benchmarks.py` asserts `Bash` stays out of that matcher.
Invoking `post_tool_use` directly with a shell tool name does append an event and
arm the gate, but that path measures the function rather than the product.

**A zero-file event is clearable.** The receipt binds to the event's `eventHash`,
never to a file, and the criterion is one the operator declares, so the printed
remediation is satisfiable with nothing recorded under `cwd`.

**It warns; it never blocks.** `stop` prints `systemMessage`, and on Claude Code
an `additionalContext` the agent can actually read. It emits no block decision,
so an armed gate never stops a session from ending -- it states that the
completion claim is unreceipted.

**A session opened above the data root roots its durable session below.**
`_event_cwd` keeps recording provenance where the working directory is an
ancestor of the data root -- a drive root, the home directory -- so the gate
arms there by design. The durable session cannot be rooted at that working
directory: `work_state.py` refuses a workspace containing its own evidence
store, in `session_directory` and again in `source_fingerprint`. Root it at the
subdirectory the edits landed in; the receipt check asks only that the durable
session and the recorded edit share one tree, in either direction. Prefer a
subdirectory the host does not write to continuously, because the fingerprint
covers the whole workspace and a tree the host keeps writing into goes stale
between `run` and `verify`. Pointing `COGNITIVE_POWERS_DATA` outside the working
directory is the other way out, and the warning names both rather than printing
three steps whose precondition it left unstated.

Clearing the gate takes two identities: `record-validation` requires the
validator to differ from the executor. A single agent working alone therefore
cannot honestly close its own gate, and should report the armed gate rather than
self-certify. What the check compares is two strings -- stripped in the hook,
folded through `sanitize_identifier` in `work_state.py`, which is why one actor
cannot present two Unicode spellings of one name. So what it enforces is that
two names were given; what it is for is a second party that did not produce the
result, and the registered `verifier` agent is that party. A second name chosen
by the executor passes the check without satisfying it.

## Inspect durable state schema

Durable state currently uses schema version 1. Inspect one session with the
versioned migration-policy entrypoint:

```powershell
& $python skills/execute-durably/scripts/work_state.py `
  --root <workspace> `
  --data-root <external-data-root> `
  state-migrate --session <session-id> --json
```

The default and only current mode is `dry-run`. It validates `state.json`, the
authenticated ledger chain, checkpoint/delta recovery, and `recovery.json`
without writing or creating a backup.
For a current session it reports `migration_required=false`.

This checkout has no migration path because no supported state needs migration.
Unknown, future, boolean, string, or malformed schema versions fail closed; the
tool does not pretend to upgrade them. A future explicitly implemented migration
must add a versioned path and create and verify a backup before its first write.
Until then, there is deliberately no apply switch.

## Resolve lock and state failures

- `timed out waiting for state lock` means an identified live owner still holds
  the session. Age alone does not make that lock abandoned.
- A lock owned by a dead process or by a reused PID identity is reclaimed
  immediately. A malformed or unidentified lock is reclaimed only after the
  30-second stale boundary.
- An owner removes only the lock carrying its own token, so it cannot release a
  successor's lock.
- Do not manually delete a lock merely because it is old. First confirm the
  recorded process is no longer the owner.
- A malformed ledger line, broken hook hash chain, unreadable source file, or
  unsupported state schema blocks validation. Preserve the files for diagnosis;
  do not truncate or silently skip the failing record.

Atomic state replacement writes and fsyncs a temporary file before replacement.
An interrupted write, fsync, or replace preserves the previous state file and
cleans temporary residue. The write-ahead ledger can recover a newer flushed
transition from its checkpoint and deltas when replacement of `state.json` is
interrupted.

## Resume and compact a durable session

Derive a resume summary only from the fully authenticated ledger recovery:

```powershell
& $python skills/execute-durably/scripts/work_state.py --root <workspace> `
  --data-root <external-data-root> resume-summary --session <id> --json
```

The result separates completed and runnable packets. A completed packet is never
rescheduled, and an unreadable ledger or one without a recoverable checkpoint
fails closed.

Compaction requires an export path outside the session and retains a verified
checkpoint plus at least one event:

```powershell
& $python skills/execute-durably/scripts/work_state.py --root <workspace> `
  --data-root <external-data-root> compact --session <id> `
  --bundle <external-bundle.zip> --retain-events 25 --json
```

Verify a bundle against the session that produced it before relying on it or
removing it:

```powershell
& $python skills/execute-durably/scripts/work_state.py --root <workspace> `
  --data-root <external-data-root> verify-bundle --session <id> `
  --bundle <external-bundle.zip> --json
```

Authentication comes from the session, not from the archive. `compact` records
the bundle's digest in a signed `compaction_checkpoint` event in the surviving
ledger, and this command requires the bundle to match one of those. A bundle is
a byte copy of the session directory and therefore carries a copy of the ledger
key, so anything verified against the key it ships with proves only that the
archive is self-consistent -- which a forger controls. Once the recording
checkpoint has itself been compacted away, the session no longer holds the
evidence that it produced those bytes and the command refuses rather than
guessing.

Do not remove the bundle until a newer complete bundle and retained state have
both been verified. Schema migration remains forward-only with a verified copy;
there is no destructive downgrade path.

## Run the installer

Two scripts perform one transaction. `install.ps1` is the PowerShell 7 path and
`install.sh` the POSIX path for Linux and macOS; both take the release tag as an
option, default to the declared release, and are moved together by
`scripts/bump_version.py`, so neither can be left naming an older tag:

```powershell
& ./install.ps1 -ReleaseRef v1.10.0-rc.1
```

```bash
./install.sh --release-ref v1.10.0-rc.1
```

`install.sh` also accepts the `-ReleaseRef` spelling, so a documented command
transfers between hosts unchanged. Both resolve the tag to a 40-character commit
through `gh` before reading the profile, copy the configured marketplace to a
recovery directory before any removal, and restore the previous installation --
from the pinned remote commit when that is possible, from the recovery copy
otherwise -- if any step fails. A recovery copy that had to be used is preserved
and named in the failure message, and a later run recognizes it and resumes the
upgrade from it rather than refusing it.

Run either script as a file from its own checkout. The canonical verifier is
resolved beside the script (`$PSScriptRoot` and `${BASH_SOURCE[0]}`
respectively), and both refuse in preflight when it is not there, before the
profile is read or changed. A copy fetched on its own has no postcondition to
run, and until 1.8.1 that was discovered only at the end: the whole transaction
completed and was then rolled back, reporting an empty-string parameter binding
rather than a verifier that was never fetched.

Pass `-Help` or `--help` for the options and the interpreter override.

Where PowerShell supplies something the shell does not, `install.sh` marks the
substitution with a `DIVERGENCE` comment and says why it is safe. The ones worth
knowing at the console:

| PowerShell | POSIX port | Consequence |
|---|---|---|
| `python` | `python3`, then `python` | PEP 394 makes a bare `python` absent on most distributions. `COGNITIVE_POWERS_PYTHON` overrides the choice and is honoured by both scripts, so one documented variable pins the interpreter on either host. |
| `ConvertFrom-Json` | the preflight-verified interpreter | The interpreter is proven runnable before the first document is parsed, in the position `install.ps1` already proves it. |
| `${XDG_DATA_HOME:-$HOME/.local/share}`, and `GetFolderPath("LocalApplicationData")` on Windows only | `${XDG_DATA_HOME:-$HOME/.local/share}` | One rule on every host both scripts run on, so a recovery marketplace written by either is recognized by both. `install.ps1` used to ask .NET everywhere, which answers this rule only on Linux: on macOS it reports `Library/Application Support` under the account's own home and consults neither variable, so each script refused to resume from the other's recovery copy. A macOS recovery copy preserved by `install.ps1` before 1.9.0 is under `~/Library/Application Support/cognitive-powers` and has to be moved to `~/.local/share/cognitive-powers` to be recognized. |
| `[IO.Path]::GetFullPath` | `cd` plus `pwd -P` | Resolves symlinks rather than normalizing lexically, which is what a symlinked `$TMPDIR` or `$HOME` requires. |
| `[ValidatePattern]` | an explicit check before the preflight | The expected version is a substring of the ref, so a malformed ref would be verified against nonsense. |

Both scripts exit `1` on any failure, so a caller cannot tell them apart by
status.

## Verify an installed release

After an explicitly authorized installation, run the canonical read-only
verifier against the immutable tag and reported installed root:

```powershell
& $python scripts/verify_installed.py --source-root . `
  --installed-root <installed-root> --tag v1.10.0-rc.1
```

The marketplace must be pinned to the tag's resolved 40-character commit SHA.
Exit `10` means tag/identity failure, `11` content mismatch, `12` public surface
or host inventory mismatch, and `13` host CLI failure. Preserve the JSON output
before changing the installation. The offline CI gate uses only a synthetic Git
tag and sets `CODEX_HOME`, `HOME`, and `USERPROFILE` to a disposable home; it
never opens or repairs the real Codex profile.

## Validate and update

Install only the pinned development checker, then run validation with its JSON
receipt outside the repository:

```powershell
& $python -m pip install -r requirements-dev.txt
& $python scripts/validate_all.py --offline `
  --json-output <outside-repo-validation.json>
```

GitHub Actions runs the same offline surface on Ubuntu, Windows, and macOS with
Python 3.11 and 3.13. It exercises the OS-specific hook lock before the canonical
validator. The workflow contains no release command or secret dependency.
Validation and optional receipt artifact publication remain separate.

The compatibility gate regenerates status only from supplied CI receipts and
compares it to `compatibility-baseline.json` and `docs/compatibility.md`. With no
receipts, all 108 declared combinations remain `unknown`. Never edit an unknown
row to compatible manually.

On an exact release tag, CI builds the tagged archive and manifest twice and
requires byte-identical archive hashes and identical manifests. The manifest
fails closed for an additional tag at the release commit or any changed public
surface. This creates candidate artifacts only; publication, tag creation, and
release creation require separate authorization.

## Release checklist

Tags are immutable: a tag, once pushed, is never moved or deleted. The plugin
cache on both hosts is keyed by version, so a moved tag can never invalidate an
installation -- every correction is a new version. The 1.7.1 tag was moved
repeatedly before this rule existed, which is exactly how a machine ended up
running a pre-fix tree that reported the fixed version.

1. Write the new `CHANGELOG.md` section first; the publisher's notes derive
   from it and refuse an empty section.
2. `& $python scripts/bump_version.py <X.Y.Z>` moves every carrier and derives
   the documented rollback target; `--check` runs in the suite from then on.
3. Full local gate, push, branch CI green, then `git tag vX.Y.Z` and push the
   tag. The tag run must be green end to end. Between the bump commit and the
   tag push the README documents a clone command whose ref does not resolve
   yet; the `documented-release-ref` job asks the remote on the nightly
   schedule, on dispatch, and on the tag itself, so a bump left untagged
   overnight turns Validate red rather than shipping a README that lies. Close
   the window in the same session, or move the carriers back.
4. Nothing. `AUTO_PUBLISH` is `true`, so the green tag run dispatches
   `publish-release.yml` itself; `verify-release.yml` then re-checks the assets
   and the release body against the changelog, and its `record-published-release`
   job commits the new tag into `docs/releases.json` on `main`.

   That entry used to be a manual post-publication step, and it was the only
   step no gate could watch: it ran after every gate had finished, on a release
   that was already out. It failed the way that shape of step always eventually
   fails -- v1.10.0-rc.1 shipped and was never recorded, so the documented rollback
   target would have skipped past it. There is no longer a `DRY_RUN_RELEASE_REF`
   variable either; the nightly dry run reads the newest entry of the same file,
   which is now written by the job that proved the release real rather than by
   whoever remembered.

A release candidate takes the same four steps with `X.Y.Z-rc.N` as the version.
It is a version and not an annotation on one: the changelog heading, both
manifests, the marketplace entry, both installer defaults and the README clone
command all carry the suffix, because the publisher binds the tag to the
version the tagged manifest declares and those two literals have to agree. The
accepted labels are `alpha`, `beta` and `rc`, ranked in that order below the
release they precede; anything else is refused rather than ranked by rules
nothing here implements. Two things deliberately do not move. The documented
rollback target stays the newest published *release* -- a reader escaping a bad
build is not recovered by being sent to a candidate -- and
`record-published-release` skips a prerelease tag entirely, so `docs/releases.json`
keeps listing only the releases a rollback may name. The publisher passes
`--prerelease`, derived from the tag rather than from an input that could
disagree with it, so GitHub keeps resolving "latest" to the newest real
release.

Local installations are the one thing still worth doing by hand, and only when
an update is intended: a same-version cache is never refreshed in place.

Updating an installed development plugin is a separate, explicitly authorized
operation. After validation and only when an update is intended:

```powershell
codex plugin add cognitive-powers@personal --json
codex plugin list --json
```

The reported installed version must match `.codex-plugin/plugin.json`; restart
Codex before relying on the refreshed installation.

## Run controller A/B without persistent working-tree growth

The live runner now places `homes/`, `runs/`, `storage/`, evaluator fixture
clones, provider event streams, and messages under an external ephemeral work
root. The batch coordinator likewise keeps per-job `sessions/` outside the
final evidence directory. After compact receipts, diffs, summaries, and hashes
have been re-read and validated, a successful default run deletes that work
root. `batch-status.json` records the actual pre-cleanup file count and bytes
and the post-cleanup values:

```powershell
& $python scripts/controller_ab_batch.py `
  --config <frozen-batch-config.json> `
  --output <final-evidence-directory> `
  --work-root <external-empty-work-directory>

$status = Get-Content <final-evidence-directory>/batch-status.json | ConvertFrom-Json
$status.ephemeral_cleanup
$status.final_evidence_measurement
```

Run the same frozen pilot config once with `--preflight` into a distinct empty
output before the scored pilot. Schema-v3 configs must include the absolute
canonical `plugin_source`; the coordinator verifies its clean Git identity and
the runner hashes the installed runtime against it. After independently reviewed pilot and
promotion runs have completed in separate roots, create the immutable combined
bundle:

On Windows, keep `--work-root` short because nested plugin caches can exceed
legacy path limits. Mutation preflights must also prove that the host honored
write access. If `workspace-write` is downgraded to read-only, stop the batch;
use `write-batch-config --bypass-sandbox` only after explicit authorization and
freeze a new configuration identity.

```powershell
& $python scripts/finalize_controller_ab_evidence.py `
  --coordinator-output <pilot-evidence-directory> `
  --coordinator-output <promotion-evidence-directory> `
  --bundle-output <empty-combined-bundle-directory> `
  --verifier-receipt <host-independent-verifier-receipt.json> `
  --controller-protocol benchmarks/controller_ab_protocol.json `
  --task-contract benchmarks/evaluation_tasks.json

& $python scripts/integration_evaluation.py `
  --receipts <combined-bundle-directory>/session-receipts.jsonl `
  --tasks benchmarks/evaluation_tasks.json `
  --controller-protocol benchmarks/controller_ab_protocol.json `
  --artifact-index <combined-bundle-directory>/sha256-index.json
```

The verifier receipt must be host-provenanced, scoped to the experiment, use
the `experiment-verifier` role, be independent of every experiment runner, and
bind the exact sorted SHA-256 values of all supplied coordinator indexes.
Finalization is fail-closed and leaves both coordinator roots byte-identical.

`persistent_file_count` and `persistent_total_bytes` must both be `0` after a
successful default run. `final_evidence_measurement` is the converged exact file
count and bytes of the compact coordinator output, including
`batch-status.json` itself. The `--work-root` directory must not exist before
the first run and must not overlap the evidence output. Omit it to use an
operating system temporary directory.

For diagnosis only, `--retain-debug-workdirs` preserves successful work state.
The default is false. An abnormal failure or interruption is always fail-closed:
the coordinator preserves the external state needed to diagnose the ambiguous
provider job, writes `debug-workdir.json`, and reports its exact absolute path,
file count, and byte count. The journal continues to refuse an automatic retry
of a started, failed, or interrupted provider job.

Copying is bounded before a destination is created. Configure
`max_work_files` and `max_work_bytes` in the batch config, or pass
`--max-work-files` and `--max-work-bytes` to the live runner. Each task may
declare `fixture_manifest` as an explicit list of fixture-relative files or
directories. Contract-bound Git fixtures otherwise copy tracked files only;
legacy isolated fixtures use the same bounded exclusion policy. Bulky excluded
dependency or generated trees fail clearly. The diagnostic
`allow_large_excluded_trees` / `--allow-large-excluded-trees` override must be
explicit.

The compact coordinator evidence consists of `frozen-manifest.json`,
`randomized-schedule.json`, `session-receipts.jsonl`, `agent-events.jsonl`,
`pre-evaluator-diffs/`, `hidden-check-results.jsonl`,
`quality-check-results.jsonl`, and `analysis-with-ci95.json`. The independent
verifier subsequently creates a separate combined bundle containing those
artifacts plus `sha256-index.json` and `independent-verdict.json`.
`batch-journal.jsonl`,
`coordinator-sha256-index.json`, and `batch-status.json` remain only because the
current fail-closed coordinator and verifier consume them. No successful
default output contains `sessions/`, `homes/`, `runs/`, or `storage/`.

### Measured complete-protocol baseline

The frozen 80-fixture corpus plus four preflight pairs schedules 244 jobs and
488 sessions. The previous implementation persisted 490 `CODEX_HOME` trees
(including the two templates), 1,464 batch-output fixture copies, and 488
storage trees. The fixture total is 1,624 persistent trees: 1,464 runner copies,
80 materialized actor trees, and 80 materialized evaluator trees. With the
actually materialized frozen corpus and the same minimal 112-file,
912,991-byte home, the baseline was **152,088 files and 501,104,698 bytes**,
excluding provider-written storage and logs. Runner fixture copies accounted
for 92,232 files / 52,551,174 bytes; actor trees for 5,040 files / 2,871,676
bytes; evaluator trees for 160 files / 142,240 bytes; and home copies for 54,656
files / 445,539,608 bytes.

For that same fixture and home, the successful default after-measurement is
**0 persistent working files and 0 persistent working bytes**: all 488 session
work trees are sequential ephemeral state, and only compact evidence remains.
The command above makes this measurement executable for a real or synthetic
batch; read the two `persistent_*` fields from `batch-status.json` rather than
inferring cleanup from a process exit code.

## Inspect and collect durable storage

Durable evidence uses one content-addressed object under
`objects/sha256/<prefix>/<digest>` for identical artifacts. Session evidence
paths use hard links when the filesystem supports them and retain hash
verification either way. Ledger transitions use authenticated bounded deltas,
create a full recovery checkpoint every 32 events, and compact after 128 events.

Inspect storage before deciding whether collection is warranted:

```powershell
& $python skills/execute-durably/scripts/work_state.py `
  storage-inspect --largest 20 --json
```

The report includes logical and physical bytes, file count, project count,
session count, and the largest directories. Garbage collection is a dry run
unless `--apply` is explicit:

```powershell
& $python skills/execute-durably/scripts/work_state.py `
  storage-gc --older-than-days 30 --keep-last 5 --json

& $python skills/execute-durably/scripts/work_state.py `
  storage-gc --older-than-days 30 --keep-last 5 --apply --json
```

Active sessions, live locks, unreadable state, recent completed sessions, and
the newest requested sessions remain protected. Use `compact --session <id>`
to verify recovery and compact a specific ledger immediately.
