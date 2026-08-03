# Eval harness: adopt, extend, or build

Input for the activation-eval mission. It records what the candidate harnesses measure, what they
cannot measure here, and the one decision that follows.

Mechanisms are described in our own words. No prose or code was copied from any source. References
use the form `repo · path:line` and point into clones kept outside this tree at `C:\dev\reference\`;
they are never committed here. Both new sources entered `integrations/catalog.json` as `candidate`
before this evaluation ran, because a status set to justify a decision already taken is the failure
mode that file exists to prevent.

## What has to be measured

One metric, stated before looking at any candidate, so the candidates are scored against the
mission rather than the mission against the candidates.

For each of nineteen workflows: **the rate at which the real host invokes that workflow, on prompts
phrased the way requests arrive, under three configurations of this plugin's own injected context** —
and the rate at which it invokes any workflow on prompts where none should fire.

Four properties of that sentence do the work:

1. **Per skill.** Nineteen workflows share one catalogue. "Some workflow fired" is not the
   measurement; `CLAUDE.md` already records that the danger here is siblings stealing each other's
   prompts.
2. **Both polarities.** Under-triggering and over-triggering are different defects and a
   configuration can have both at once. The off-domain floor is the binding constraint on all
   activation work.
3. **Three environment arms.** No injection, standing instruction only, index plus instruction.
   The arms differ in what this plugin's *hooks* do, not in what its skill files contain.
4. **Invocation observed, not claimed.** Parsed from the transcript. A model that says it consulted
   a workflow has said something; it has not done something.

## Candidate 0 — `claude plugin eval` (first party)

Found while probing the host, not part of the brief. The shipped Claude Code CLI has a plugin eval
runner: `evals/**/case.yaml`, `--runs` defaulting to 3, `--threshold` for gating, `--json`,
`--ablation with-without` for a no-plugin baseline arm, and LLM graders under `graders/*.md`.

It is the closest thing to a native answer and it is **unavailable**. Every invocation on this
machine, including `--help` on its `init` subcommand, exits 1 with `plugin eval is currently in
early access`. A harness that cannot be run cannot be adopted, and a mission that gates CI on it
would gate CI on an entitlement.

Two things it settles anyway. `evals/` with per-case YAML is the shape the host will eventually
expect, so our layout should not gratuitously differ. And `claude plugin details
cognitive-powers@cognitive-powers` reports the plugin's always-on cost as **~3,318 tokens** with
hooks listed as `harness-only — no model context cost`. That number is the denominator for the
index question in Phase 2.7: the session-start index adds ~742 tokens of catalogue on top of ~3,318
tokens of catalogue the host already loads.

## Candidate 1 — `adewale/skill-eval-harness`

MIT, v0.6.0, commit `2297000f`, published to PyPI. This is a serious harness and its
`run_trigger_matrix.py` is the closest existing implementation of the metric above.

### What it gets right

- **Autonomous activation is the thesis, not a side effect.** Its module docstring states that
  activation is a property of the skill *and* the model *and* the harness, not of the skill alone
  (`skill-eval-harness · run_trigger_matrix.py:4-6`). Cells are keyed `(agent, model)`.
- **Detection from real evidence.** For Claude it parses `claude -p --output-format stream-json
  --verbose` and looks for assistant `tool_use` blocks named `Skill` whose `input.skill` is in the
  set actually mounted. Never prose.
- **Both polarities are first class.** `should_trigger` is a real JSON boolean, validated as one
  because Python truthiness would read the string `"false"` as `True` and silently invert the
  measurement (`skill-eval-harness · run_pi_trigger_eval.py:324-342`). It is lifted into a
  two-member enum, and pass is defined exactly once as `triggered == expectation.should_trigger`
  (`trigger_contracts.py:319-331,519-521`). Every cell carries `should_trigger` and
  `should_not_trigger` cohorts beside the overall one (`trigger_reporting.py:239-246`).
- **An incomplete run is not a zero.** Rates exist only on the complete cohort variant; an
  incomplete or empty cohort has no rate field to serialize at all
  (`trigger_reporting.py:3-5,139-170`). Per-provider stream-integrity gates demote a malformed or
  truncated stream to an incomplete observation rather than scoring it as "did not fire"
  (`run_trigger_matrix.py:189-209`).
- **The plan is written independently of the results.** The report envelope carries a `design` block
  enumerating every intended `(agent, model, query, polarity)` tuple, so a run that never happened
  is detectable rather than invisible (`run_trigger_matrix.py:1109-1129`).
- **No containers anywhere.** Isolation is `tempfile.TemporaryDirectory`, `cwd=workspace`, and a
  `copytree` of the skill tree. Its POSIX primitives are `hasattr`-guarded or branched on
  `os.name == "nt"`. Subscription auth works because it deliberately preserves ambient config.

That list is why this document exists at all: most of it is worth taking.

### Why it cannot be adopted as a dependency

Ordered by how fatal they are.

1. **It never installs a plugin.** It copies a skills tree into `<workspace>/.claude/skills`. There
   is no plugin, marketplace, or settings install path in the tree. This plugin's hooks *are* the
   independent variable of all three arms, and under this harness they would never be installed, so
   every arm would measure the same thing. This alone ends the adoption case: the harness cannot
   express the experiment.
2. **A case cannot name its expected skill.** A trigger case declares `should_trigger: bool` and
   nothing else. With nineteen workflows mounted, "triggered" means *some* workflow fired. That is
   precisely the granularity `CLAUDE.md` says is useless here, where the measured risk is one
   sibling winning another's prompts.
3. **Its arms are the wrong kind.** An arm is `--ablation <id>`, which materialises an *edited skill
   tree*. The arm vocabulary is closed in code to `with_skill`, `without_skill`, `old_skill`, and
   `ablation:<id>` (`skill_benchmark.py:1199-1208`). There is no environment or hook arm, and the
   only appearance of "hooks" in its documentation is as a frontmatter field of a different class
   (`skill-eval-harness · docs/skill-ablation-spec.md:67`). Three arms would be three separate runs plus our own
   comparison — which is most of a harness.
4. **Its fallback detector would manufacture false positives here.** Besides the `Skill` tool
   evidence it accepts `mounted_path` evidence — a file read under the mounted tree
   (`trigger_contracts.py:334-337`). A run that merely reads `SKILL.md` counts as triggered. Our
   negative corpus is twenty prompts (`evals/cases/should-not-fire.yaml`) whose whole purpose is to
   draw nothing; a detector that fires on a read would score noise as activation.
5. **Two runtime dependencies.** `pyyaml>=6` and an exact-pinned `regex==2026.7.19`, neither
   present here; `import skill_benchmark` fails on this machine with `ModuleNotFoundError: yaml`.
   `CLAUDE.md` forbids adding a runtime dependency, and the reason applies with full force to this
   mission: several components exist to report whether an installation works, and a harness that
   needed installing first would be measuring its own prerequisites.
6. **Platform and version fit.** Its CI runs Ubuntu on 3.10–3.12 and reduces Windows to two test
   files; 3.13, the interpreter this project requires, is never tested. `shlex.split` in POSIX mode
   mangles Windows paths in its CLI-override options. Its `ruff.toml` equivalent pins
   `required-version = "==0.16.0"` against our `ruff.toml`'s `"==0.16.1"`; that only bites if a
   vendored copy brought its `pyproject.toml` along, which is avoidable, but it is a live edge.
7. **Surface and churn.** `skill_benchmark.py` is a single hand-written module of about 21,000
   lines. Nine releases in roughly a month, with changelog entries requiring artifacts to be
   regenerated.

Points 1 through 4 are functional; the harness cannot express this experiment at the granularity the
mission requires. Points 5 through 7 would be tolerable on their own.

## Candidate 2 — `skill-bench/skill-eval-action`

MIT, commit `32cb44e7`. Proposed for the CI layer.

**It does not measure activation.** Its unit is the fraction of natural-language rubric criteria that
a second LLM call judged as passed, aggregated to one `pass_rate` against a threshold. It contains a
Skill-invocation detector (`skill-eval-action · scripts/eval.py:288-294`), but the value is written
to a metadata file and never read by aggregation, thresholding, the PR comment, or the viewer.

Worse for our purpose: on the cases where activation would matter, `expect_skill: true` causes the
whole `SKILL.md` body to be pasted into the prompt (`skill-eval-action · scripts/eval.py:326-333`),
and the skill is
never installed as a skill anywhere. There is no Skill entry for the model to invoke, and no need to
invoke one. `expect_skill` is a prompt-injection switch, not an assertion. It measures whether a
model given a workflow's text follows it — a real question, and not this one.

Beyond the metric: one skill per run, no repeated runs, no arms, `shell: bash` in every composite
step, and a hard requirement for `ANTHROPIC_API_KEY`.

Worth taking from it: the stream-json parsing shape, scrubbing `CLAUDECODE` from the environment
before invoking `claude -p` from inside a Claude session, per-case fixture trees, and validating the
whole case file before spending any money. Worth refusing: the `SKILL.md` paste, which destroys the
activation measurement by construction, and its encoding-naive file reads, which break on Windows
with non-ASCII content — our corpus is bilingual.

## Candidate 3 — `daymade/claude-code-skills`, for the calibration mission

Already catalogued (`pattern` / `approved` / `adapt-pattern`). Not a candidate for this mission's
runner; it is the model for the next one.

Its loop: `run_eval.py` measures a firing rate per query over three repetitions against a single
0.5 threshold applied in both directions; `run_loop.py` wraps that in up to five iterations with a
stratified 60/40 split on a fixed seed, asks a model for a new description while showing it *only*
training results, and selects the winner on the held-out split.

Three recorded facts matter more than the design. Its convergence guard — the case where every
candidate scored zero and the optimiser converged on the original — is documented but **not
implemented in code**. The hazard that a hook injecting a different skill first fools a probe is
documented explicitly, and the shipped probe is still first-tool-wins and still vulnerable. And the
probe never installs the real skill: it writes a synthetic command file carrying the candidate
description, so it measures description routing, not plugin activation. Its trigger probe is also
POSIX-only (`select.select` on a pipe).

## Decision

**BUILD**, informed by all three designs.

Not because the alternatives are weak — `skill-eval-harness` is better engineered than what this
mission will produce, and its incomplete-observation discipline is better than anything currently in
this repository. It is because the experiment the mission specifies is not expressible in any of
them: the independent variable is **this plugin's hook-injected context**, and none of the three
installs a plugin or has an environment arm. Adoption would mean writing the plugin mount, the
per-skill attribution, the arm mechanism, and the comparison — and then owning a fork of a
21,000-line module and two dependencies for what remained.

The decision matrix, scored against the metric declared above.

| | `plugin eval` | `skill-eval-harness` | `skill-eval-action` | Build |
|---|---|---|---|---|
| Per-skill activation attribution | unknown | **no** (pooled) | no | yes |
| Both polarities scored | unknown | **yes** | no | yes |
| Environment/hook arms | baseline only | **no** | no | yes |
| Installs *this plugin* under test | yes | **no** | no | yes |
| Detection from Skill tool_use | unknown | yes, plus a read fallback | present but unused | yes |
| Repeated runs and per-case variance | `--runs` | runs, counts only | no | yes |
| Windows 11 / Python 3.13 native | n/a | partial, untested on 3.13 | bash-only action | yes |
| Runtime dependencies | n/a | pyyaml + regex | pyyaml + node | **none** |
| Available on this account | **no** | yes | yes | yes |
| Integration effort | blocked | high (4 gaps to fill) | very high (wrong metric) | medium |
| Maintenance risk | n/a | fork of a 21k-line module | fork of a grader | our own code |

For the CI layer the same conclusion follows for a narrower reason: `skill-eval-action` grades
rubrics, so gating our activation rate on it would gate on a number it does not compute. CI gets its
own workflow.

### What is ported, conceptually

From `skill-eval-harness`: activation detected only from a `Skill` tool_use naming an installed
workflow; both polarities as one pass rule rather than two metrics; repetition per case because a
single run is a coin flip; the discipline that a run which did not complete is *incomplete*, not a
zero, and that a cohort with no complete runs has no rate at all; and a declared plan written before
the results so a missing run is visible. That last group matches this repository's existing rule —
fail closed, and say what failed.

From `skill-eval-action`: scrubbing the inherited `CLAUDECODE` environment before spawning
`claude -p`, per-case fixture trees, and validating the corpus before spending anything.

From `daymade`: the 60/40 held-out split and its two recorded failures — no convergence guard, and a
first-tool-wins probe — both of which the calibration mission must implement rather than document.

### What is refused

- **Path-evidence fallback.** Reading a workflow file is not invoking it.
- **Pasting a workflow body into the prompt.** It answers a different question and cannot be mixed
  with this one.
- **Any runtime dependency.** Standard library only, consistent with the rest of the tree.
- **Pooled "some skill fired" detection.** Nineteen siblings make it meaningless.

## What the probing established about the host

Facts measured on this machine while evaluating the candidates. They constrain the build and none of
them came from a candidate's documentation.

- `--plugin-dir <path>` loads a plugin for one session and registers it with the source
  `cognitive-powers@inline`. The `pluginConfigs` key that supplies `python_executable` is therefore
  `cognitive-powers@inline`; the installed plugin's key does not apply and the hooks fail to start
  without it.
- `--plugin-dir` **replaces** the installed plugin set: a session started with it reports only the
  inline plugin.
- `CLAUDE_CONFIG_DIR` isolates settings but hides the credentials, so the run ends at
  `Not logged in`. Isolation must come from `--plugin-dir`, `--setting-sources`, and the working
  directory instead.
- Activation appears as an assistant `tool_use` block named `Skill` whose `input.skill` reads
  `cognitive-powers:<workflow>`.
- **Claude Code loads both hook manifests.** `hooks/hooks.json`, the Codex manifest, is picked up in
  addition to the `hooks/hooks.claude.json` the plugin declares, so every hook runs twice. Verified
  by falsification: deleting only `hooks/hooks.json` from a copy took `SessionStart` from four hook
  events to two and `UserPromptSubmit` from two to one. On Windows the duplicate always fails
  (`python3` resolves to the Microsoft Store stub), which is why it has been invisible; where
  `python3` resolves, the index and the standing instruction are injected twice. The harness must
  detect this per run, because an arm measured under a double injection is not the arm.

There is no `--max-turns`. Cost control is `--max-budget-usd`, a restricted tool set, and stopping
the process once the activation decision has been observed.
