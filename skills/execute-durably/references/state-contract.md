# Durable state contract

Read this reference before starting or resuming an `execute-durably` session.

## Storage

State is external to the repository. Resolution order:

1. `COGNITIVE_POWERS_DATA`
2. `PLUGIN_DATA`
3. `~/.codex/cognitive-powers`

This order is host-independent, and the selective edit hook resolves it the
same way. The final entry keeps its historical name so one machine running
both hosts keeps a single store; set `COGNITIVE_POWERS_DATA` to relocate it.
Do not introduce a host-specific data variable here: the hook and the receipt
writer run in different processes, and only a root both can resolve lets the
`Stop` gate match a receipt to the edit it covers.

Each repository receives a SHA-256-derived project key:

```text
<data-root>/projects/<project-key>/sessions/<session-id>/
  brief.md
  state.json
  ledger.jsonl
  evidence/
```

The resolved data root must be outside the target repository; every command rejects an in-repository `--data-root`, `COGNITIVE_POWERS_DATA`, or `PLUGIN_DATA` before creating session files.

`state.json` is the current snapshot. `ledger.jsonl` is the append-only write-ahead history and includes a recovery snapshot that is omitted from compact `status` output. Events are flushed before the atomic `state.json` replacement, so `load_state` can recover if the snapshot write is interrupted. Every ledger line must be a complete JSON event; malformed lines fail closed instead of being skipped, including a truncated final write.

A PID-, process-creation-identity-, and token-bound lock file serializes state mutations across processes. Age alone never makes an identified live lock abandoned: a lock with a dead recorded owner or a confirmed creation-identity mismatch is reclaimed immediately, while a malformed or unidentified lock must be at least 30 seconds old. If a live process's creation identity is temporarily unreadable, ownership is preserved conservatively. An owner removes the lock only while its token still matches, so it cannot release a successor's lock.

## Criterion lifecycle

```text
pending -> in_progress -> claimed -> verified
               |    |       |          |
               |    v       v          v
               |   red   rejected    stale
               v    |       |          |
             failed +---- reopen <------+
```

The session becomes `complete` only when every criterion is `verified` and every receipt still matches the current source fingerprint.

Optional work packets have a separate `planned -> active -> completed` lifecycle. All declared packet checks must pass against the packet's current owned-path fingerprint. Packet completion never changes criterion or session status.

## Commands

All commands accept global `--root` and optional `--data-root` before the subcommand.

- `init`: create a session without overwriting an existing one.
- `status [--json]`: show current criteria, stale or invalid evidence, paths, and latest ledger entries.
- `plan-packets`: install one validated dependency graph from a JSON file or stdin.
- `start-packet`: assign and activate a packet after its dependencies complete.
- `run-packet-check`: execute one immutable declared argv check and retain its receipt.
- `complete-packet`: close a packet only while all check receipts remain valid.
- `reopen-packet`: reopen a completed packet only after its evidence becomes invalid. It returns completed descendants to `planned` atomically, refuses to proceed while any descendant is `active`, and reactivates a previously completed session.
- `run`: execute an argv command without a shell and record its actual result.
- `run-red`: execute a regression command that must fail and bind it to the defective source fingerprint.
- `run-green`: rerun the exact red command after a source change and require success.
- `record`: copy and hash a non-empty artifact for manual or external evidence.
- `record-context`: copy, hash, and bind normalized versioned documentation to a criterion.
- `record-communication`: copy and bind normalized communication usage plus its original provider record.
- `record-design`: copy and bind a completed visual-review contract and every declared render artifact.
- `verify`: record an independent `confirmed`, `rejected`, or `inconclusive` verdict.
- `reopen`: return a non-complete criterion to pending with an explicit reason.
- `complete`: close only when all evidence and verification gates pass.

## Evidence validity

Command receipts include argv, exit code, stdout/stderr hashes and tails, executor, timestamp, and source fingerprint. Artifact receipts additionally include the copied artifact path and SHA-256. External-context receipts also bind provider, library ID, requested and matched version, query, provider-response hash, retrieval time, and expiry. Communication receipts preserve provider/model metadata, success and quality status, separate token counters, the normalized receipt, and the hash-identical provider record; they do not infer a counterfactual.

Design receipts preserve the intent identity, reviewer, mobile and desktop renders, review inputs, associated browser receipt, and artifact hashes. They remain ineligible as behavioral verification and never establish objective aesthetic quality.

Test-cycle receipts bind the immutable red receipt to the green result. The red command must exit nonzero, the green command must use identical argv and exit zero, and their source fingerprints must differ. A test that already passes, a changed test command, or an unchanged source cannot establish a red/green cycle.

A command claim is eligible for confirmation only when its recorded exit code is zero. Artifact evidence must remain non-empty and hash-identical. External documentation must also remain unexpired. All evidence becomes stale when the source fingerprint changes.

The source fingerprint intentionally excludes VCS data, dependencies, generated output, caches, media, and durable-state storage. It proves identity only for the source-oriented surface hashed by the tool; state this limitation when broader external state matters. Enumeration or read failures inside the included source surface fail closed; an unreadable file cannot silently disappear from the identity.
