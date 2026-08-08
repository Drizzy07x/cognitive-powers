---
description: Iterative bug hunt and fix loop with verification after every change
argument-hint: [optional path, directory, or area to scope the run]
---

# Bug Fix Loop

Work through the repository in repeated passes: find real bugs, fix them one at a time, verify after every change. The run ends when the checks are green and the findings list is empty — not when the first fix lands.

**Scope:** `$ARGUMENTS`

If no scope is given, treat the whole repository as in scope. If a scope is given, restrict discovery and edits to it, but still run repository-wide checks so regressions elsewhere are caught.

---

## 1. Discovery

Detect the stack and the checks the repository already provides. Do not assume a language or toolchain.

Look for:

- `package.json` — the `scripts` block (typecheck, lint, test, build) and the package manager (`pnpm-lock.yaml`, `yarn.lock`, `bun.lockb`, `package-lock.json`)
- `tsconfig.json`, `jsconfig.json` — TypeScript configuration and strictness level
- `eslint.config.*`, `.eslintrc*`, `biome.json`, `.prettierrc*`
- `pyproject.toml`, `setup.cfg`, `tox.ini`, `pytest.ini`, `requirements*.txt`, `ruff.toml`, `mypy.ini`
- `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle*`, `Gemfile`, `composer.json`, `*.csproj`
- `Makefile`, `Justfile`, `Taskfile.yml`
- `.github/workflows/*`, `.gitlab-ci.yml`, `azure-pipelines.yml` — where CI exists it is the authoritative list of checks

Record the exact command for each of: **typecheck, lint, test, build**. Mirror CI when CI exists. If a category has no command in this repository, note it as unavailable rather than inventing one.

## 2. Baseline

Run every available check once, before touching any code, and record:

- the command, its exit code, and a short summary of the output
- which failures already exist on a clean tree

Pre-existing failures are part of the picture and also the reference point: any failure that appears later and is not in the baseline was introduced by a fix, and must be resolved before moving on.

If the working tree is dirty at the start, say so and ask before continuing — an unrelated in-progress change makes verification unreliable.

## 3. Hunt

Collect real defects. Sources, in order of reliability:

1. Failures from the baseline checks — type errors, failing tests, build errors, and lint rules that encode correctness rather than style
2. Manual reading of the code in scope

Look for:

- unawaited promises and floating async calls
- missing or swallowed error handling — `catch` blocks that discard, unchecked error returns
- unguarded `null` / `undefined` / `None` access, and unchecked results of optional chaining
- off-by-one errors in loops, slices, and index arithmetic
- race conditions: shared mutable state, unsynchronised concurrent writes, missing cancellation on teardown or abort
- resource leaks: unclosed files, sockets, streams, subscriptions, timers, database handles
- effect and lifecycle dependency lists that are incomplete, over-broad, or prone to stale closures
- state mutated in place where the surrounding code assumes immutability
- boundary handling: empty collections, zero, negative values, very large inputs
- comparisons and coercions that are wrong for the type — `==` vs `===`, float equality, mixed-type sorts

**Out of scope:** formatting, naming, import order, comment wording, and any other purely stylistic preference. If a linter flags it and it cannot cause wrong behaviour, skip it.

## 4. Triage

Before editing anything, list every finding with file, line, what breaks, and why.

Order by severity:

1. **Breaks in production** — data loss, crashes on real input, security-relevant defects, incorrect results reaching users
2. **Breaks the build** — compile, typecheck, or build failures
3. **Incorrect behaviour** — failing tests, wrong output under conditions that occur in practice
4. **Latent risk** — correct today, fragile under a plausible input, ordering, or concurrency change

Present this list before starting the fix loop.

## 5. Fix loop

Repeat, one bug per iteration, highest severity first:

1. Restate the bug and the intended fix in one or two lines.
2. Apply the **smallest change that fixes it**. Touch only the lines the fix requires.
3. Re-run the checks relevant to the change — the affected test file, the typecheck, the affected package — then the full set if the change is broad.
4. Confirm two things: the bug is gone, and nothing that passed before now fails.
5. If the fix breaks something else, revert it, note why the direct fix does not work, and reconsider the approach. Do not stack a second fix on top of a broken one.
6. Record the outcome and move to the next bug.

Never batch several unrelated fixes into one unverified change.

## 6. Guardrails

- No refactors beyond what the fix requires.
- No rewriting whole files. Edit in place.
- Do not change public API signatures, exported types, or on-disk formats without asking first.
- Do not add, remove, or upgrade dependencies without asking first.
- Do not delete, skip, or weaken tests to make a run pass. A failing test is either a real bug or a wrong test — say which.
- Do not hand-edit generated files, lockfiles, or vendored code.
- Do not commit, push, tag, or open a pull request unless explicitly asked.
- Leave unrelated pre-existing failures alone unless they block verification; report them instead.

## 7. Stop conditions

Stop when any of these is true:

- All available checks pass and the triage list is empty. **(Success.)**
- Ten iterations have run. Report progress and remaining findings.
- The same bug fails verification twice in a row. Stop there — do not try a third variation. Report what was attempted, what failed, and what decision is needed.
- A fix would require something the guardrails block. Ask, then wait.

## 8. Report

Close with:

- **Fixed** — one line per bug: what was wrong, what changed, and the check that proves it
- **Skipped** — findings left alone, and why (out of scope, needs a decision, pre-existing and unrelated)
- **Unresolved** — anything attempted and reverted, with the blocker
- **Files touched** — full list
- **Final check status** — command and exit code for typecheck, lint, test, and build
