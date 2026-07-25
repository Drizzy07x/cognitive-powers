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

## Inspect durable state schema

Durable state currently uses schema version 1. Inspect one session with the
versioned migration-policy entrypoint:

```powershell
& $python skills/execute-durably/scripts/work_state.py `
  --root <workspace> `
  --data-root <external-data-root> `
  state-migrate --session <session-id> --json
```

The default and only current mode is `dry-run`. It validates `state.json`, every
ledger line, and embedded state snapshots without writing or creating a backup.
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
snapshot when replacement of `state.json` is interrupted.

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
verifier subsequently adds `sha256-index.json` and
`independent-verdict.json`. `batch-journal.jsonl`,
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
verification either way. Ledger transitions append bounded deltas, create a
full recovery checkpoint every 32 events, and compact after 128 events.

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
