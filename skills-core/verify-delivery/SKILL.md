---
name: verify-delivery
description: Audit an already-completed delivery against real evidence. Use when asked to review finished work or double-check whether something just finished actually works, and for completion claims, tests, Git state, whether a release is ready to ship, or a benchmark; never while implementing a change.
---

# Verify Delivery

Audit falsifiable completion claims without extending the implementation scope.

## Locate plugin files

Paths written as `scripts/<file>` are relative to this skill's own directory.
Paths written as `<plugin-root>/...` are relative to the installed Cognitive
Powers root: the directory that contains `skills/`, `scripts/`, and a
`.codex-plugin/plugin.json` or `.claude-plugin/plugin.json` manifest. Resolve
both from this skill's own location rather than guessing, and never copy plugin
scripts into the target repository.

## Bounded audit fast path

When the checkout contains an explicit claim and a small declared evidence surface, do not load another Cognitive Powers file. The Construction checks below stay in force for a code delivery - the bound narrows what is read, not what a diff owes. Use two tool calls after this skill read:

1. **One audit batch.** Read the claim and applicable instructions; capture `git status --porcelain --untracked-files=all`, exact local/tracking/remote revisions, the relevant diff or artifact object/hash comparison, and the declared test path. Inspect only files named by the claim or status. Do not inspect author configuration, signing, commit history beyond the claimed revision, unrelated files, or alternate test frameworks unless the claim includes them.
2. **One verification.** Run the single declared/focused test command once. Do not try pytest, Pester, compile checks, or default discovery after a relevant suite passes.

If the claim names a Python suite but no command, infer the runner from repository configuration. For a plain `tests/test_*.py` tree with no pytest configuration, use the available Python runtime with `-m unittest discover -s tests -p <matched-test-file>`; do not guess pytest or execute a unittest module as a bare script. In the verdict, say explicitly that local `HEAD` and `origin/main` **match** at the exact SHA when they do. In a claim about local/remote Git identity, “identity” means the named revision SHA; do not inspect personal `user.name`, email, author, committer, signature, history, fsck, or unrelated branch metadata unless the claim explicitly names one of them.

Then report one mixed verdict: state whether delivery is complete, identify every contradicted tracked/untracked/artifact claim, preserve facts that did pass, and include the exact revision and test outcome. Do not re-read files, rerun checks, create closeout payloads, or invoke `knowledge_closeout.py`.

## Escalate only when genuinely broad

Read `../../skills/verify-delivery/SKILL.md` only when the claim spans several independent products, runtime hosts, review perspectives, or unclear evidence boundaries. A release label by itself does not justify escalation.

## Construction checks

For a code delivery, audit naming, defensive programming, and error handling
in the diff: an identifier the change made misleading, an assertion reachable
by user input, or a newly silent catch is a finding cited by line. Re-read the
whole diff as its own pass, distinct from writing, and say so in the verdict.

## Verify

Run meaningful current checks where possible. Missing tools, skipped checks, stale reports, and inspection alone are never a pass; return a concrete gap when evidence is insufficient.

## Pause points

DO-CONFIRM: work from judgment, then stop and confirm each item; name any
unconfirmed item in the verdict. Before evidence: claims listed and mapped to
what could falsify them. Before the verdict: each claim classified from
current evidence; construction checks run on code. Before declaring complete:
verdict bound to the exact revision reviewed; gaps carry the smallest next
check.
