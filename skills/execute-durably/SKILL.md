---
name: execute-durably
description: Execute long or compaction-prone work with external state, evidence receipts, and independent verification. Use for resumable multi-turn work with several observable criteria.
---

# Execute Durably

Use durable state only when its recovery value exceeds its overhead. Keep state outside the repository so public projects do not acquire workflow artifacts.

## 1. Initialize observable criteria

Read [state-contract.md](references/state-contract.md), then initialize one session with the complete objective and falsifiable criteria:

Set `$python` to a working Python 3 executable first. In Codex desktop, use the bundled path returned by the workspace dependency loader before considering an installation; verify it with `& $python --version` and do not rely on a Microsoft Store alias.

```powershell
& $python <skill-root>/scripts/work_state.py --root <repo> init --session <id> --objective "<outcome>" --criterion "<criterion 1>" --criterion "<criterion 2>"
```

The script writes under `COGNITIVE_POWERS_DATA`, `PLUGIN_DATA`, or the fallback `~/.codex/cognitive-powers`; it never writes state into the target repository.

## 2. Resume from state, not conversation

At the start of every later turn or after compaction, run `status --json` and read the reported brief, state, and latest ledger events before acting. Continue the first unresolved criterion. Do not rebuild the plan from memory.

For a compact board, timeline, blocker list, and handoff derived from that same state and ledger, run:

```powershell
& $python <skill-root>/scripts/coordination_report.py --state <session-dir>/state.json --ledger <session-dir>/ledger.jsonl
```

The report is a view, not a second state store. Do not edit it to change packet status.

Delegate only independent work. Every worker prompt must name the deliverable, scope, and verification target. Keep one owner for coupled edits. Read [agent-roles.md](references/agent-roles.md) before assigning executor, test-writer, or verifier roles; custom TOML agents are optional and must not become an installation requirement.

For a medium or large implementation with independent file ownership, read [work-packets.md](references/work-packets.md), compile human-authored Markdown when applicable, and install one atomic packet plan before starting workers. Do not use packets for a focused edit. Packet checks are scoped implementation gates; they never replace integrated criteria or independent verification.

## 3. Capture evidence through the tool

Use `run` for executable checks so the real exit code and output hashes are recorded:

```powershell
& $python <skill-root>/scripts/work_state.py --root <repo> run --session <id> --criterion c1 --executor <agent-id> -- <command> <args>
```

For a regression test, record the defective and corrected states with the exact same command:

```powershell
& $python <skill-root>/scripts/work_state.py --root <repo> run-red --session <id> --criterion c1 --executor <agent-id> -- <test-command> <args>
# Change the source only after run-red demonstrates failure.
& $python <skill-root>/scripts/work_state.py --root <repo> run-green --session <id> --criterion c1 --executor <agent-id> -- <test-command> <args>
```

`run-red` succeeds only when the command fails. `run-green` requires the identical argv, a changed source fingerprint, and a zero exit code. The resulting test-cycle receipt binds both source states and both outputs.

Use `record` for visual or external artifacts; the tool copies and hashes the artifact into external evidence storage. A successful command creates a `claimed` criterion, not a completed one.

When the plugin's selective edit hook is enabled, read [hook-evidence.md](references/hook-evidence.md) before clearing its `Stop` warning. The hook is an observability aid and never substitutes for this session's completion gate.

For a successful normalized receipt from `$verify-web-behavior`, use `record-web`. It rejects failed, empty, malformed, in-repository, or hash-mismatched evidence and copies every declared Playwright artifact into the durable session:

```powershell
& $python <skill-root>/scripts/work_state.py --root <repo> record-web --session <id> --criterion c1 --executor <agent-id> --receipt <cognitive-playwright-receipt.json>
```

For a completed normalized receipt from `$explore-web-adaptively`, use `record-navigation` only when the criterion is discovery itself. The typed receipt remains `navigation_only` and cannot substitute for `record-web` on a behavioral criterion:

```powershell
& $python <skill-root>/scripts/work_state.py --root <repo> record-navigation --session <id> --criterion c1 --executor <agent-id> --receipt <cognitive-skyvern-receipt.json>
```

For a completed `$design-intentionally` visual contract, use `record-design` only when the criterion is visual review or fidelity. It copies every declared intent, review, browser, and screenshot artifact and remains non-behavioral:

```powershell
& $python <skill-root>/scripts/work_state.py --root <repo> record-design --session <id> --criterion c1 --executor <agent-id> --receipt <cognitive-design-receipt.json>
```

For a successful normalized receipt from `$operate-desktop-adaptively`, use `record-desktop`. It copies and hashes the QCU receipt and raw transcript. The receipt must show real actions, correct foreground focus, no stale capture or rejected input, explicit objective verification, and deliberate finish:

```powershell
& $python <skill-root>/scripts/work_state.py --root <repo> record-desktop --session <id> --criterion c1 --executor <agent-id> --receipt <cognitive-qcu-receipt.json>
```

Desktop evidence may support a behavioral criterion, but it remains `claimed` until a different verifier confirms that the final evidence actually proves the criterion.

A normalized semantic-navigation result may be captured to preserve why files or tests were selected, but it is navigation evidence only. It cannot complete a behavioral criterion without the selected executable check.

Use `record-context` for a normalized payload produced by `$use-current-docs`. It records the selected library ID, requested and matched version, exact query, provider-response hash, and expiry. Expired context cannot be verified or used to complete a session.

Use `record-communication` for a provider-backed usage receipt produced by `$communicate-efficiently`. It copies and hashes both the normalized receipt and original provider record. This evidence proves recorded usage only; it does not prove task correctness or a missing counterfactual baseline.

```powershell
& $python <skill-root>/scripts/work_state.py --root <repo> record-communication --session <id> --criterion c1 --executor <agent-id> --receipt <communication-receipt.json>
```

## 4. Require independent verification

Give a fresh verifier the original objective, relevant diff, criterion, and receipt without revealing an expected verdict. Only `confirmed` can advance a claim:

```powershell
& $python <skill-root>/scripts/work_state.py --root <repo> verify --session <id> --criterion c1 --verifier <different-agent-id> --verdict confirmed --note "<why evidence proves the criterion>"
```

The script rejects self-verification, malformed receipts, missing or changed artifacts, and evidence made stale by later source changes. A different identifier is a guardrail, not proof of cognitive independence; the workflow must actually use a fresh context.

## 5. Close only through the gate

Run `complete` only after every criterion is verified. Any pending, failed, blocked, rejected, inconclusive, or stale criterion blocks completion without a retry limit or fail-open escape.

Do not create commits, branches, PRs, security reviews, or external publications unless the user requested them.
