---
name: verify-installation
description: Establish whether this installed plugin actually runs on the current host, by executing its interpreter, hooks, evidence storage, and optional providers instead of inspecting its packaging.
when_to_use: Use when the plugin has just been installed, enabled, relocated, or taken an update, when its hooks or durable receipts appear not to work, or when a host upgrade may have changed what it can rely on.
disallowed-tools: Edit, Write, NotebookEdit
---

# Verify Installation

Packaging validity and working software are different claims. A manifest can be
well formed while the interpreter never resolves, the hooks never fire, or two
components disagree about where evidence lives. Report only what was observed.

## Locate plugin files

Paths written as `scripts/<file>` are relative to this skill's own directory.
Paths written as `<plugin-root>/...` are relative to the installed Cognitive
Powers root: the directory that contains `skills/`, `scripts/`, and a
`.codex-plugin/plugin.json` or `.claude-plugin/plugin.json` manifest. Resolve
both from this skill's own location rather than guessing, and never copy plugin
scripts into the target repository.

## 1. Run the observed checks

Set `$python` to a working Python 3 executable and verify it with
`& $python --version` first. On Windows the `python3` alias under `WindowsApps`
is a Microsoft Store stub that exits without running Python.

```powershell
& $python <plugin-root>/scripts/selfcheck.py --json
```

Every check runs something. Writes happen only in temporary directories, so
this is safe in a repository you do not own. Report each `fail` with the exact
detail string; do not summarise several failures into one sentence.

A `skipped` optional provider is a supported configuration, not a defect. Say
which capability is unavailable as a result rather than implying breakage.

The report opens with the root and the version it checked, and `host.claude_code`
and `host.codex` say which version each host actually activated. A `skipped`
there means that host has no installation record, which is what a staged tree
and CI look like. A `fail` means the checks below it describe a tree the host
does not load, so report it before anything else they establish.

## 2. Add what the script cannot see

The script runs beside the host, not inside it, so two facts are only
observable from this conversation. State both explicitly:

- Which Cognitive Powers skills appear in your own available-skills listing. A
  skill installed on disk but absent from that listing cannot be reached by the
  model, whatever the manifest declares.
- Whether the plugin's agent roles are registered and invocable. Name them as
  the host exposes them, which may be a plugin-scoped identifier.

Report a discrepancy between the installed tree and either observation as a
finding. Do not infer that a skill is routable because its file exists.

Compare the listing against the activated version rather than against the tree
on disk. A listing short by exactly the workflows added after that version is
the drift `host.claude_code` reports, seen from the other side.

## 3. Run the packaging checks only when relevant

For manifest, asset, and drift questions, run the separate packaging
diagnostic:

```powershell
& $python <plugin-root>/scripts/doctor.py --json
```

Its findings describe declarations on disk. Never present them as evidence that
anything ran.

## 4. Report

Lead with whether the installation is usable on this host. Then give failed
checks with their details, unavailable optional providers and what that costs,
and the two host observations. Name the plugin root and version you tested.

If everything passed, say the checks that ran and what they establish. Do not
extend that to compatibility with hosts, versions, or platforms that were not
exercised here.

## Pause points

DO-CONFIRM: work from judgment, then stop at each point and confirm every item. An unconfirmed item goes in the report, never silently past it.

**Before reporting health**
- Components were executed, not inspected as packaging.
- The interpreter, hooks, and storage ran on this host, in this checkout.

**Before claiming done**
- Observed, skipped, and unavailable are reported as three different things.
- Optional providers failed soft and are named as absent, not broken.
