# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Cognitive Powers is a plugin that ships from **one source tree to two hosts**: Codex reads
`.codex-plugin/plugin.json` + `skills-core/` + `hooks/hooks.json`; Claude Code reads
`.claude-plugin/plugin.json` + `skills/` + `hooks/hooks.claude.json`. Both manifests declare the
same version, and `doctor.py` reports `versionsAligned: false` when they drift.

**The two skill trees are not mirrors.** `skills/` holds all nineteen workflows. `skills-core/`
holds only the three routers Codex installs — `solve-efficiently`, `execute-durably`,
`verify-delivery` — with their own `SKILL.md` and `agents/openai.yaml`, which differ from the
`skills/` copies and are gated separately. Codex reaches the other sixteen by reading
`skills/<name>/SKILL.md` under the plugin root; that is why `hooks/skill_router.py` names a Skill
tool id on Claude Code and a file path on Codex, and why the catalog in
`skills-core/execute-durably/SKILL.md` has to list every specialized workflow by path.

Everything is Python standard library, and 3.11 is the floor: CI runs 3.11 and 3.13, and the
`python_executable` user config states the same minimum, so a 3.12-only construct passes on the
machine that wrote it and fails half the matrix. `ruff` is the only dependency, dev-only and
hash-pinned in `requirements-dev.txt`. Do not add a runtime dependency: several components exist to report whether
an installation works, and one that needed installing first would be self-defeating.

## Commands

```powershell
# The canonical gate. 25 commands: the unittest suite, ruff, eleven benchmark runners,
# packaging contracts, and doctor. The receipt MUST land outside the repo.
& $python scripts/validate_all.py --offline --json-output <path-outside-repo>.json

# The whole unittest suite, the way the gate's `tests` command runs it
& $python -m unittest discover -s tests -v

# One module, one class, or one test
& $python -m unittest tests.test_work_state_storage
& $python -m unittest tests.test_plugin_hooks.PluginHookTests.test_post_tool_use_reads_stdin_appends_and_hashes_changed_file

& $python -m ruff check .
& $python -m ruff format --check .

& $python scripts/validate_skills.py --strict-quality
& $python scripts/doctor.py --json                          # packaging, never executes a host CLI
& $python scripts/doctor.py --validate-installation --json  # runs the components in a temp copy
& $python scripts/run_skill_routing_benchmarks.py           # after any skill name/description edit

# Readability findings. Exit 1 when any remain; a waived one is not reported.
& $python hooks/clean_code_guard.py --scan hooks
'{"tool_name":"Write","tool_input":{"file_path":"hooks/skill_router.py"}}' |
    & $python hooks/clean_code_guard.py post-tool-use
```

`$python` must be an explicit interpreter path. On Windows `python3` resolves to a Microsoft Store
stub that exits without running Python — the reason `python_executable` is required user config with
no default.

Reading the receipt: `offlinePassed` is whether the 25 commands succeeded. `passed` additionally
requires a clean worktree and a stable source identity, so it is `false` on any dirty tree by
design. `skippedTests` is recorded per command because a skipped assertion is one that did not run.

**A green local gate is not the full gate.** `.github/workflows/validate.yml` runs those same
commands across three operating systems, both Python versions, and two lockfile-pinned Codex CLIs
under `ci/`. The release path is layered on top of that: manifest reproducibility and witness on
every push, the real install/upgrade/rollback nightly or on dispatch, the whole path only on a tag.
Nine of the thirteen 1.7.1-era defects lived in steps a tag push alone ran, and the layering exists
so that class of masking cannot re-form.

Three runners are deliberately outside the gate. Two fail locally without their provider:
`run_semantic_benchmarks.py` (CodeGraph) and `run_browser_benchmarks.py` (Playwright). The third,
`evals/run_activation_eval.py`, spawns the real `claude` binary and costs money on every case, which
is the same reason by a different route — but everything it delegates to is pure and does run in the
gate, so a defect in the judgement is caught offline even though the measurement is not.

`docs/operations.md` is the runbook for everything the gate does not do: lock and state-schema
recovery, durable resume across a compaction, verifying an installed release, the release
checklist, and running the controller A/B without growing the working tree.

## Architecture

**The routing decision has one implementation.** `scripts/skill_routing.py` holds `decide()`, and
both `hooks/skill_router.py` and the routing benchmark call it. A hook carrying its own copy could
satisfy every checked-in case and rank something else at runtime — that split is what the module
exists to prevent. Skill descriptions are the routing corpus: the benchmark scores the combined
`description` + `when_to_use` text because that is what the host actually lists.

**That match is lexical, so a description that names a sibling claims its vocabulary.** Ceding
territory in prose — "not for X, that is `other-skill`" — puts `other-skill`'s words in this
skill's own bag and wins the prompts it meant to hand over. Measured on `refactor-cleanly`: with
two such clauses, one Spanish prompt misrouted and Spanish routing fell to 0.92; removing them
returned 0 misroutes and 0.94. Separate siblings in the body, which the router never scores.

**The per-skill corpus measures disambiguation between siblings; the `natural` corpus measures
whether a skill fires at all.** The per-skill prompts were written against the descriptions, so
under-triggering is invisible to them by construction — 59 of 60 reach their own skill while
"clean up this module, it is hard to read" named `map-project` and "help me implement pagination"
named nothing. `natural` in `benchmarks/skill_routing_cases.json` is that blind spot measured: 43
prompts in the register a request actually arrives in, one per skill at minimum, scored through
`decide` rather than the ranking beneath it, held to `min_natural_rate`. Two prompts additionally
carry `outranks`, pinning a misroute that was seen once so a rate cannot absorb its return.

Its floor is 0.75 against a measured 0.79–0.81, which is slack a rate this young needs and not a
verdict that the remaining eight misses are acceptable; tighten it as the corpus earns a history,
the way the Spanish floor went 0.80 → 0.93. Two limits are structural rather than pending work.
A green `natural` still says nothing about whether the model *invokes* the workflow the hook names
— that is `evals/`. And a request carrying no vocabulary any listing declares ("why does the login
redirect loop?") cannot be reached by a lexical scorer at all; it is checked in as a miss on
purpose, because deleting it would make the corpus flatter the router.

**`evals/` measures activation; the routing benchmark measures ranking.** They answer different
questions and neither substitutes for the other. `run_skill_routing_benchmarks.py` scores
`skill_routing.decide` on prompts written against the descriptions, so a workflow that never fires
on natural phrasing is invisible to it. `evals/run_activation_eval.py` spawns the real `claude`
binary against a corpus written from the SKILL.md *bodies* and reads which workflows the session
actually invoked. It is outside the gate for the same reason the semantic and browser runners are —
it costs money — but everything it delegates to is pure and tested: `evals/activation_core/`
holds the YAML subset loader, the corpus rules, the transcript reader, the arm definitions and the
scorer. Two invariants there are load-bearing. Detection is **structural only** — an assistant
`tool_use` named `Skill` whose `input.skill` names an installed workflow — because the router
injects the literal text `cognitive-powers:<name>` into the same stream and a substring scan would
score the harness's own instrumentation. And a run whose observed injections disagree with its arm,
including one delivered twice, is recorded as *incomplete* rather than scored: these hooks degrade
silently by contract, so an arm that never took effect otherwise reads as an arm that changed
nothing.

**Five hooks, three shapes.** `hooks/semantic_index.py` and `hooks/skill_activation.py`
(both SessionStart) and `hooks/skill_router.py`
(UserPromptSubmit) are advisory in full and stay silent on every error. `hooks/selective_hooks.py`
is not: it records the edit ledger that the `Stop` completion gate reads, so a dropped event is
indistinguishable from a session that changed nothing. When editing it, ask what an early return
makes invisible. `hooks/clean_code_guard.py` (PostToolUse on writes) is the third shape: advisory
by default, exit 2 under `CLEAN_CODE_GUARD_STRICT`, with the measurable rules isolated in
`hooks/clean_code_rules.py` — pure analysis, no I/O, no process control. Waivers in
`cleancode-accepted.txt` are per `path:line:rule`, never per file, and hook mode ignores them: the
file being edited right now is the one where a stale waiver would hide the next defect.

**Durable state lives outside the repository**, at `~/.codex/cognitive-powers` on both hosts
(historical name, deliberately shared so one machine keeps one store), overridable with
`COGNITIVE_POWERS_DATA`. `skills/execute-durably/scripts/work_state.py` is the CLI; the real logic
sits beside it in `skills/execute-durably/scripts/work_state_core/` — `durability.py` (ledger, HMAC
chain, recovery), `storage.py` (content-addressed objects, garbage collection), and
`mutation_probe.py`, which the gate runs directly rather than through the CLI. `_default_data_root()` in `selective_hooks.py` must stay
byte-identical to `resolve_data_root()` in `durability.py`: if they diverge, receipts land outside
the root the Stop gate checks and it rejects work that is in fact complete.

**`agents/` ships six subagents** (`executor`, `test-writer`, `verifier`, `investigator`,
`researcher`, `reviewer`) whose frontmatter withholds `Agent` from the tool set. The depth-one rule
is enforced by what the tools allow, not by what the prompt asks for — a worker able to spawn
workers breaks it whatever its instructions say. `verifier` adds `disallowedTools` and
`isolation: worktree` for the same reason: the agent that produced a result cannot be the one
confirming it. The last three are `READ_ONLY_ROLES` in `scripts/orchestration_policy.py`, so their
tool sets refuse the edit tools too; `investigator` also takes `isolation: worktree`, because it is
the other role granted `Bash` and a granted `Bash` is a write path whatever the prompt says.

**Adding a role moves two directories, not one.** `test_agent_files_mirror_the_codex_roles`
compares the `agents/*.md` stems against `.codex/agents/*.toml` and fails on any difference, so a
Claude role shipped without its Codex counterpart is a red suite rather than a half-registered
role. Both files are derived from the same contract in
`skills/execute-durably/references/agent-roles.md`; the counts stated in `README.md`,
`docs/features.md`, and this file are prose that no test can check.

**Fail closed, and say what failed.** Corruption, unreadable evidence, unknown schema versions and
torn writes raise a domain error rather than guessing or tracebacking. `UnicodeDecodeError` is a
`ValueError`, not an `OSError` — a handler guarding only `OSError` lets a half-written file escape.

**`mcp/evidence_server.py`** publishes read-only inspections of that store over MCP. It shells out
to the canonical `work_state.py` subcommand rather than reimplementing the read path, and its tool
table is the allowlist: a name not in it reaches no subprocess. Mutation stays on the CLI.

**`scripts/orchestration_policy.py` is what the A/B measures.** It selects execution intensity and
the conservative host-agent plan from a request's declared signals, and `solve-efficiently` evaluates
an explicit planning packet through it. Reading the harness below without reading this one leaves the
experiment with no subject: the arms differ in what this module is allowed to return, not in what the
runner does.

**The controller A/B harness** (`scripts/controller_ab_*.py`, `live_ab_runner*.py`,
`prepare_controller_ab_homes.py`) is the only part that calls a provider. `controller_ab_fixtures.py`
*generates* the fixture trees and the `hidden_check.py` / `quality_check.py` evaluators in-process —
nothing under `benchmarks/` is read as evaluator material — and refuses to materialize inside the
repo. `INSTALLED_SURFACE_DIRECTORIES` / `INSTALLED_SURFACE_FILES` in `live_ab_runner.py` define what
counts as the shipped runtime surface; the A/B homes copy exactly that.

`benchmarks/controller_ab_protocol.json` freezes the design and its `not-proven` state. Evidence
scope is promotion-only, so a pilot-only run proves nothing by construction. The control arm is
`forced-solo` of the same build, not "no plugin".

**`integrations/catalog.json` records what may be taken from an outside source, and how.** Every
external repository the project has looked at is a `source` with a `kind`, a `status`, and a
`decision` that constrains the use: `external-only` and `external-adapter` never vendor code,
`adapt-pattern` and `clean-room-pattern` take the idea and not the implementation, and
`discovery-only`, `rejected`, and `benchmark-reference-only` take neither. Check it before importing
an upstream idea — the decision was already made once. `scripts/external_catalog.py validate` runs in
the gate and holds `VALID_TRANSITIONS`, so a status cannot be quietly promoted to justify a change
already written. Its `kind: "provider"` rows are what `scripts/doctor.py` reports, and it reports
them as declarations: `networkProbed` and `executablesProbed` stay false and `availabilityUnknown`
stays true, because a provider named in a catalog is not a provider present on the host.

## Invariants worth knowing before you edit

- **All nineteen skills stay model-invocable.** `userInvocableOnlySkills` must be empty. The core
  workflows delegate to the specialized ones by name, and Claude Code hides a
  `disable-model-invocation` skill from the model entirely, so one moved there becomes unreachable.
  Asserted by `tests/test_claude_plugin_contract.py`.
- **Adding or removing a workflow moves eight carriers.** The `skills/<name>/` directory,
  `SPECIALIZED_SKILLS` in `tests/test_claude_plugin_contract.py`, `CLAUDE_WORKFLOW_COUNT` in
  `scripts/verify_installed.py`, the catalog in `skills-core/execute-durably/SKILL.md`, the
  `skills` array in `benchmarks/skill_routing_cases.json`, and that file's `spanish` and `natural`
  corpora. Those three are not optional extras: `run_skill_routing_benchmarks.py` raises
  `ValueError` when the case names and the skill names are not the same set, and again when
  `natural` does not cover every skill, and
  `test_spanish_cases_cover_every_skill_and_its_own_quiet_corpus` requires one Spanish case per
  skill. Registering a skill in the corpus is not tuning it — the benchmark refuses an unregistered
  skill rather than scoring it as perfect by omission. The eighth is
  `evals/cases/should-fire.yaml`: `run_activation_eval.py --validate-only` exits 1 when a workflow
  has fewer than three should-fire prompts, so a new workflow that nobody wrote prompts for is
  reported as unmeasured rather than silently scoring nothing. Then rerun the benchmark: a new description
  changes the ranking of prompts that were never about it. A Spanish case whose content words
  `SPANISH_TERMS` in `scripts/skill_routing.py` cannot translate ranks first yet draws no
  suggestion — that is how 1.8.0 shipped four silent cases — and the 0.93 Spanish floor now fails
  the benchmark on it, which makes the lexicon a carrier too. Its own rules are test-enforced:
  every translation must land on a word some listing declares (skill names count), no key may be
  spelled like an English word unless the mapping is identity, and no Spanish stopword may be
  spelled like an English content word.
- **Adding a hook moves three assertions in `tests/test_claude_plugin_contract.py`**: the number of
  hook entries, the set of script names, and the subcommand table. Every hook takes its event as
  `args[1]`, and the table reads that index directly, so a hook registered without one raises
  `IndexError` instead of reporting a missing subcommand.
- **This file is gated like the code.** `tests/test_documentation.py` resolves every
  repository-relative path and markdown link in the six `ROOT_DOCUMENTS` (`README.md`, `CLAUDE.md`,
  `THIRD_PARTY_NOTICES.md`, and the three community documents) plus `docs/*.md`, and fails when a
  new root document is not listed there. It cannot catch a wrong count in prose — 1.7.3 shipped
  three such claims — so counts stated here are worth rechecking against the tree before trusting
  them.
- **`docs/compatibility.md` is generated, not written.** The `compatibility-contract` command in the
  gate re-derives it from `compatibility-contract.json` with `--check`, so a hand edit fails
  validation rather than surviving as documentation. Change the contract; the table follows. The
  same holds for its `unknown` cells: they mean no receipt was bound to that commit, and editing one
  to `pass` states the opposite of what the matrix is for.
- **Every skill needs `agents/openai.yaml`** with a 25–64 character `short_description` and a
  `default_prompt` mentioning `$<skill-name>`. Skills cap at 500 lines, and past 180 lines
  `--strict-quality` demands a `references/` directory. The host truncates `description` +
  `when_to_use` at 1,536 characters in the listing. Every workflow closes with DO-CONFIRM
  pause-point checklists, and since 1.8.1 `validate_skills.py` enforces that structurally in plain
  mode: the section, the DO-CONFIRM opener, and ten items per block are errors, not conventions.
  The `skills-core/` routers owe the heading and opener only — their checklists are compressed
  prose by design. `docs/extraction-matrix.md` records where the 1.8.0 rules came from.
- **Version carriers move only through `scripts/bump_version.py`.** Write the `CHANGELOG.md`
  section first — the bump refuses to run without it, and the publisher derives release notes from
  it. Tags are immutable: every correction is a new version, because the plugin cache on both hosts
  is keyed by version and a same-version cache is never refreshed in place.
- **`source.sha256` identifies a commit, not a checkout.** Text is folded to LF and filenames
  composed to NFC before hashing (`sha256-text-normalized-v3`), so platforms agree. Digests from
  different schemes are not comparable and are rejected rather than reported as a content change.
- **A test that mocks the integration under test proves nothing.** This has bitten twice: a
  `subprocess.run` mock hid that prepared A/B homes could not work on any host, and a hand-listed
  CLI gate silently shrank to half the shipped scripts. Prefer enumerating the real surface.

## Style

Comments explain **why a defect was possible**, not what a line does — read a few before writing
one. The `CHANGELOG.md` entries follow the same rule and are the best available record of intent.
Match each file's existing voice; keep diffs scoped.
