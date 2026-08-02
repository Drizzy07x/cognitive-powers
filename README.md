<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.png">
    <img src="assets/logo.png" alt="Cognitive Powers" width="720">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/Drizzy07x/cognitive-powers/actions/workflows/validate.yml"><img src="https://github.com/Drizzy07x/cognitive-powers/actions/workflows/validate.yml/badge.svg" alt="Validate"></a>
  <a href="https://github.com/Drizzy07x/cognitive-powers/releases/latest"><img src="https://img.shields.io/github/v/release/Drizzy07x/cognitive-powers" alt="Latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/Drizzy07x/cognitive-powers" alt="License: MIT"></a>
</p>

# Cognitive Powers

Coding agents are confident about work they have not checked, and they lose that
work when the conversation compacts. Cognitive Powers is a plugin for Claude Code
and Codex that gives an agent nineteen workflows for matching the amount of
process to the task, storing evidence outside your repository so it survives a
restart, and closing a claim only when a separate verifier confirms it. It is
pure Python standard library with no runtime dependency, so nothing has to be
installed before it can tell you whether it works.

## Install in 30 seconds

**Claude Code** — add the marketplace straight from GitHub, then install:

```text
/plugin marketplace add Drizzy07x/cognitive-powers
/plugin install cognitive-powers@cognitive-powers
```

Claude Code prompts once for **Python 3 executable**. This value is required and
has no default because no interpreter name resolves correctly on every platform.
On Windows, `python3` resolves to the Microsoft Store alias in `WindowsApps`,
which exits without running Python; point the setting at the real `python.exe`.
Confirm the choice before entering it:

```powershell
& <path-to-python> --version
```

Hooks are invoked in exec form, so this path is passed as an argument vector and
is never expanded by a shell. To move to a later release, refresh the catalog
with `/plugin marketplace update cognitive-powers`, then
`/plugin update cognitive-powers@cognitive-powers`. If the clone fails because
no SSH key is configured, set `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` and retry.

To develop against a local checkout instead, use `/plugin marketplace add
<path-to-checkout>` with the same install command, or place the checkout at
`~/.claude/skills/cognitive-powers/`, where any directory carrying
`.claude-plugin/plugin.json` loads as `cognitive-powers@skills-dir` on the next
session with no marketplace and no install step.

**Codex** — the installer resolves the release tag to an immutable commit
through GitHub CLI before it reads or changes the profile, so `gh` must be
installed and authenticated. `install.ps1` and `install.sh` are the same
transaction; run whichever your host has, from a checkout, because each resolves
the canonical verifier beside itself:

```powershell
git clone --branch v1.8.2 --depth 1 https://github.com/Drizzy07x/cognitive-powers
./cognitive-powers/install.ps1
```

```bash
git clone --branch v1.8.2 --depth 1 https://github.com/Drizzy07x/cognitive-powers && ./cognitive-powers/install.sh
```

Both create a local recovery copy before any removal, pin the marketplace to
that commit SHA, verify exactly one enabled entry at the named version, and
restore the prior state if a step fails. Pass `-ReleaseRef v1.7.2` (or
`--release-ref`, which `install.sh` also accepts) for an immutable rollback to
an earlier published tag; 1.6.0 and 1.7.0 exist only as changelog sections,
never as tags. Restart Codex before starting a new task. The release and
local-development routes are mutually exclusive; the
[Operational guide](docs/operations.md) covers switching between them, and the
divergences the POSIX port had to make.

## Quickstart: three flows

Use the smallest flow that fits the work.

1. **Focused solve** — invoke `/solve-efficiently` for a bounded implementation,
   diagnosis, or research task. It selects only the context and checks the
   request justifies, and activates host-native agents only after bounded
   discovery proves independent work is worth the coordination cost.
2. **Durable execution** — invoke `/execute-durably` when work spans several
   steps, agents, or compactions. Criteria, command exits, source fingerprints,
   and independent verification stay outside the target repository.
3. **Delivery verification** — invoke `/verify-delivery` with the original claim
   and the relevant checkout. It reports Contract and Quality separately and
   does not turn missing or stale evidence into success.

These are prompt-level flows. They do not install optional providers or
authorize publication, live browser actions, or desktop input.

## Choose a skill

Codex invokes a workflow as `$name`, Claude Code as `/name`. Claude Code lists
all nineteen; Codex lists the three core routers and loads a specialized
workflow from them when the task matches.

**Core** — the three entry points.

| Skill | What it does |
|---|---|
| `$solve-efficiently` | Cross-file work, a defect with a supplied reproduction, or a bounded decision from primary sources, using progressive context discovery and verified completion. |
| `$execute-durably` | Long or compaction-prone work against external durable state, with a hash-bound receipt per criterion that only a separate verifier can close. |
| `$verify-delivery` | Audit finished work against real evidence, separating what the evidence supports from what was only asserted. |

**Specialized** — the sixteen the core workflows delegate to by name.

| Skill | What it does |
|---|---|
| `audit-capabilities` | Recommend skill updates, additions, or removals from repeated procedures rather than recurring topics. |
| `communicate-efficiently` | Pick the shortest reporting profile that still preserves the evidence. |
| `design-intentionally` | Turn visual intent into inspectable constraints, then verify the rendered result. |
| `design-review` | Judge structure against named depth red flags — shallow modules, leaked internals, vague names. Findings only. |
| `diagnose-systematically` | Find a defect's cause through a runnable signal, minimization, falsifiable hypotheses, and regression evidence. |
| `eli5` | Explain a paper or dense artifact in plain language without quietly upgrading its claims. |
| `engineer-prompts` | Build or audit a version-neutral prompt contract with testable outcomes and explicit stop conditions. |
| `explore-web-adaptively` | Discover unfamiliar browser workflows through an installed Skyvern. Discovery only, never the final judge. |
| `legacy-safe-changes` | Land a change in untested code behind a seam and characterization tests: the net comes first. |
| `map-project` | Build compact project memory holding only the facts the tree cannot cheaply reveal. |
| `operate-desktop-adaptively` | Operate and verify native Windows applications through an installed QCU, producing hashed evidence. |
| `refactor-cleanly` | Improve readability and maintainability without changing observable behavior. |
| `research-systematically` | Frozen pre-registration, labeled confirmatory and exploratory runs, every claim bound to evidence. |
| `use-current-docs` | Retrieve version-matched external documentation for the dependency release the repository actually installs. |
| `verify-installation` | Establish whether an installed copy really runs on this host by executing it, not by inspecting its packaging. |
| `verify-web-behavior` | Verify known browser behavior through a configured Playwright, capturing machine-readable evidence. |

## How verification looks

`/verify-delivery` reports two verdicts that never average into one. Below is a
trimmed real audit of this repository's own commit `386dc22`, a documentation
change to `CLAUDE.md`:

```text
Contract — verified
  Scope             1 file, +51/-8, documentation only     git show --stat 386dc22
  "25 commands"     OFFLINE_COMMANDS == 25                 scripts/validate_all.py
  "3 OS x 2 Python x 2 Codex CLI"   matrix matches         .github/workflows/validate.yml:37-39
  "0.93 Spanish floor"   min_spanish_rate: 0.93            benchmarks/skill_routing_cases.json:11
  Promised checks   9 tests OK                             unittest tests.test_documentation
                    skill validation passed                validate_skills.py --strict-quality

Quality — verified, no completion-blocking finding
  Naming / defensive programming / error handling: not applicable, the diff
  adds no code path. Review pass ran as its own step; no unrelated edit was
  absorbed into the commit. Worktree clean at audit time.

Not tested
  The full 25-command gate was not rerun for this audit, and the 108 CI
  compatibility cells stay `unknown` locally by construction.

Verdict bound to 386dc22. If the source or evidence changes, this is stale.
```

Each row cites the command, file, or line that supports it. A missing tool, a
skipped check, or a stale report is never counted as a pass — it appears under
*Not tested*, and a claim with no adequate evidence is reported `unverified`
rather than rounded up.

## Learn more

| Document | What it covers |
|---|---|
| [Feature surface](docs/features.md) | The full capability inventory, host packaging, agent roles, the evidence MCP server, and the offline/optional capability matrix. |
| [Evidence and validation](docs/evidence.md) | What each check proves, the canonical offline gate, doctor, installed-copy verification, and the controller evaluation protocol. |
| [Operational guide](docs/operations.md) | Updates, lock and state-schema recovery, durable resume across a compaction, the release checklist, and the local-usage-counter abstention. |
| [Compatibility matrix](docs/compatibility.md) | Generated per-cell status. Missing evidence is `unknown`, never assumed compatible. |

Context7, CodeGraph, Playwright, QCU, and Skyvern are optional. Cognitive Powers
works without them and never installs or initializes them inside a target
repository implicitly.

## License

Released under the [MIT License](LICENSE).

Third-party components and referenced projects remain subject to their own
licenses. Every audited source, its observed license, and its adoption decision
are recorded in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and
`integrations/catalog.json`. No third-party source is vendored: the adapters for
Playwright, Skyvern, and QCU are original implementations, and nothing under
`ci/` beyond `package.json` and `package-lock.json` is tracked.
