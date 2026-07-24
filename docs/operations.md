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
