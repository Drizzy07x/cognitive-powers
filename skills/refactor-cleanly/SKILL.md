---
name: refactor-cleanly
description: Refactor existing code for readability and maintainability - naming, function size, nesting depth, duplication, comments, error handling and test clarity - without changing observable behaviour. Use this skill when the code already works and the cost is comprehension: cleanup requests, files described as messy or hard to follow, dead code and stale comments, or when a change lands in a function that is long, deeply nested or duplicated.
when_to_use: Use when the code already works and only reading it is painful: a module called a mess, requests to tidy or clean something up, a long function, deep nesting, a misleading name, duplicated logic that has diverged, dead code, or a stale marker left behind.
---

# Refactor Cleanly

Turn working-but-unreadable code into code that stays cheap to change, without breaking behaviour.

Use this when the code already exists and the cost is comprehension, not capability. `solve-efficiently` decides what to build and `execute-durably` builds it; this workflow changes only how the result reads.

## Non-negotiable rule

**Refactoring must not change observable behaviour.**

When no test covers the code being changed, either write a characterization test that pins current behaviour first, or state in the report that the change is unverified and why. Never put a behaviour change and a refactor in the same commit.

## Locate plugin files

Paths written as `scripts/<file>` are relative to this skill's own directory.
Paths written as `<plugin-root>/...` are relative to the installed Cognitive
Powers root: the directory that contains `skills/`, `scripts/`, and a
`.codex-plugin/plugin.json` or `.claude-plugin/plugin.json` manifest. Resolve
both from this skill's own location rather than guessing, and never copy plugin
scripts into the target repository.

## 1. Scope the change

Identify exactly which files are in scope and do not expand the blast radius. Default scope is the files already touched in this session plus their direct callers.

## 2. Measure before judging

Run the guard over the scope to get objective violations:

`<plugin-root>/hooks/clean_code_guard.py --scan <path> [<path> ...]`

It reports function length, parameter count, nesting depth, branch count, file length, commented-out code, unresolved markers and long lines. A repository may argue a finding away in `cleancode-accepted.txt`, one `path:line:rule` per line; read the reason recorded there before re-fixing what someone already decided to keep.

Machine findings are the floor, not the ceiling. Read the code and add what a scanner cannot see: wrong names, leaked abstractions, temporal coupling, boolean flag arguments, misplaced responsibility.

## 3. Rank by payoff

Order the findings before touching anything:

| Priority | Condition |
| --- | --- |
| P0 | Misleading name, or duplicated logic that already diverged |
| P1 | Function that mixes decision + side effect + formatting |
| P2 | Nesting depth > 3, or function length > 20 lines |
| P3 | Cosmetic: ordering, spacing, comment cleanup |

Fix P0 and P1. Fix P2 only where it makes P0 or P1 possible. Do not spend the user's time on P3 unless they asked for a full pass. State what you deliberately left alone and why; an honest "not worth it" is a valid deliverable.

## 4. Apply in small steps

One transformation at a time, each independently revertible:

1. Rename for intent.
2. Extract the deepest block into a named function.
3. Replace nested conditionals with early returns.
4. Collapse duplication only after the third occurrence, and only when the duplicates encode the same decision rather than the same shape.
5. Push side effects such as I/O, logging and mutation to the edges.

Re-run the tests after each step; a red check reverts the step rather than patching forward. Where no test exists, re-run the guard and diff the findings.

### Smell-to-move catalog

Name the smell, apply its move, and say in the report which move fixed which smell:

| Smell | Detect | Move |
| --- | --- | --- |
| Long function | One name covering several jobs | Extract function per job |
| Feature envy | A function reading another module's data more than its own | Move the function to the data it envies |
| Shotgun surgery | One conceptual change touching many files | Gather the scattered pieces into one owner |
| Data clumps | The same group of values traveling together through signatures | Introduce a parameter object or extract the class they were hiding |
| Primitive obsession | Raw strings and numbers carrying domain rules | Wrap the primitive in a value type that owns its rules |
| Divergent change | One module edited for unrelated reasons | Split by reason to change |
| Speculative generality | Hooks, parameters, or hierarchy no caller uses | Inline it, collapse it, or delete the unused parameter |

## 5. Report

Deliver the changed files, a before/after table of guard metrics, the findings intentionally not fixed with their reason, and any behaviour risk left unruled-out.

## Core rules

Full catalogue with the smell-to-fix tables: [rules.md](references/rules.md). Read it when a review covers more than a single function, or when justifying a rejected refactor.

**Names**
- The name states intent, not implementation or type.
- Searchability scales with scope: long names for wide scope, short names for two-line loops.
- No noise words (`data`, `info`, `manager`, `helper`, `util`, `process`).
- Same concept, same word, everywhere in the codebase.

**Functions**
- One reason to exist. If the name needs "and", split it.
- All statements at one level of abstraction. Do not mix policy with plumbing.
- Zero to two parameters. Three is a smell, four demands a parameter object.
- No boolean flag parameters; that is two functions wearing one name.
- Prefer returning a value to mutating an argument.
- Early return over nested `else`.

**Comments**
- A comment that explains *what* is a failed rename. Fix the name instead.
- Keep comments that explain *why*: trade-offs, workarounds with a source, non-obvious constraints, legal notices.
- Delete commented-out code. Version control is the archive.

**Errors**
- Exceptions, not error codes. Never return `null`/`None` as a signal, never pass `null` into a function you own.
- Catch what you can act on. An empty `except` block is a bug unless the surrounding component documents silence as its contract.
- Error messages say what failed, with what input, and what to do next.

**Structure**
- Public entry points first, details below, in call order.
- Keep related things vertically close; separate unrelated things by distance.
- One level of indentation per concept, not per file.

**Tests**
- Tests obey the same naming and length rules as production code.
- One assertion concept per test.
- Fast, independent, repeatable, self-validating.
- A test you cannot read is a test nobody will maintain.

## Guardrails

- Do not rewrite a module wholesale when three targeted extractions do the job.
- Do not introduce an abstraction with a single caller.
- Do not clean generated files, vendored dependencies, or migrations.
- Style disputes with an existing formatter config always lose: the config wins.
- If a refactor forces a public API change, stop and confirm first.

## Pause points

DO-CONFIRM: work from judgment, then stop at each point and confirm every item. An unconfirmed item goes in the report, never silently past it.

**Before the first transformation**
- Scope bounded to named files; guard baseline captured.
- Uncovered code has a characterization test, or the report will say the change is unverified.
- Findings ranked; P3 cosmetics excluded unless a full pass was requested.

**After each step**
- Exactly one named move applied, independently revertible.
- Checks re-run; a red check reverted the step.

**Before claiming done**
- Behaviour change and refactor never share a commit.
- Report maps each move to the smell it fixed, with before/after guard metrics.
- Deliberately unfixed findings listed with reasons.
