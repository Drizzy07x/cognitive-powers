---
name: execute-durably
description: Execute long or compaction-prone work with external state and evidence receipts. Use for resumable multi-turn work with several observable criteria; abstain from focused single-pass tasks.
---

# Execute Durably

Use durable execution only when recovery and independent evidence justify its overhead.

## Load the workflow

Read `../../skills/execute-durably/SKILL.md` completely before taking task actions, then follow its state contract and evidence gates. Keep durable artifacts outside the target repository.

## Verify

Treat a successful command as a claim, not completion. Require the workflow's independent verification before closing a criterion, and never claim a test that did not run.
