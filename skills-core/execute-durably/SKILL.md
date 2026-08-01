---
name: execute-durably
description: Execute long or compaction-prone work with external state and evidence receipts. Use for resumable multi-turn work with several observable criteria; abstain from focused single-pass tasks.
---

# Execute Durably

Use durable execution only when recovery and independent evidence justify its overhead.

Internal workflow catalog: `../../skills/audit-capabilities/SKILL.md`,
`../../skills/communicate-efficiently/SKILL.md`,
`../../skills/design-intentionally/SKILL.md`,
`../../skills/design-review/SKILL.md`,
`../../skills/diagnose-systematically/SKILL.md`,
`../../skills/eli5/SKILL.md`,
`../../skills/engineer-prompts/SKILL.md`,
`../../skills/execute-durably/SKILL.md`,
`../../skills/explore-web-adaptively/SKILL.md`,
`../../skills/legacy-safe-changes/SKILL.md`,
`../../skills/map-project/SKILL.md`,
`../../skills/operate-desktop-adaptively/SKILL.md`,
`../../skills/refactor-cleanly/SKILL.md`,
`../../skills/research-systematically/SKILL.md`,
`../../skills/solve-efficiently/SKILL.md`,
`../../skills/use-current-docs/SKILL.md`,
`../../skills/verify-delivery/SKILL.md`,
`../../skills/verify-installation/SKILL.md`, and
`../../skills/verify-web-behavior/SKILL.md`.

## Locate plugin files

Paths written as `scripts/<file>` are relative to this skill's own directory.
Paths written as `<plugin-root>/...` are relative to the installed Cognitive
Powers root: the directory that contains `skills/`, `scripts/`, and a
`.codex-plugin/plugin.json` or `.claude-plugin/plugin.json` manifest. Resolve
both from this skill's own location rather than guessing, and never copy plugin
scripts into the target repository.

## Load the workflow

Read `../../skills/execute-durably/SKILL.md` completely before taking task actions, then follow its state contract and evidence gates. Keep durable artifacts outside the target repository.

## Execution discipline

One authority per fact: search for the existing owner before adding a
constant, format, or rule. One concern per diff: unrelated-module edits split
into their own packet. Crash early: an impossible state raises a domain error
where it is detected. Suspect this repository before the framework: an
upstream-bug claim needs a minimal reproduction outside the project. Order
criteria so the first receipt proves a thin end-to-end slice.

## Verify

Treat a successful command as a claim, not completion. Require the workflow's independent verification before closing a criterion, and never claim a test that did not run.

## Pause points

DO-CONFIRM: work from judgment, then stop and confirm each item; name any
unconfirmed item in the report. Before the first receipt: criteria falsifiable,
tracer slice first, state outside the repository. Before closing a criterion:
receipt from the tool, one concern per diff, independent verifier confirmed.
Before completing: no criterion pending or stale; no unrequested side effects.
