# Refactoring rules: smell to fix

Read this file when reviewing more than one function, or when you need to
justify why a refactor was rejected.

## Contents

1. [Naming](#naming)
2. [Functions](#functions)
3. [Control flow](#control-flow)
4. [Duplication](#duplication)
5. [Comments](#comments)
6. [Errors and boundaries](#errors-and-boundaries)
7. [State and side effects](#state-and-side-effects)
8. [Tests](#tests)
9. [When not to refactor](#when-not-to-refactor)

---

## Naming

| Smell | Fix |
| --- | --- |
| `d`, `tmp`, `res`, `val` outside a two-line scope | Rename to the thing it holds |
| Name encodes the type (`user_list`, `str_name`) | Drop the type, keep the role |
| Name encodes the mechanism (`build_json_and_post`) | Name the outcome (`publish_report`) |
| Two words for one concept (`fetch` / `retrieve` / `get`) | Pick one verb per codebase |
| One word for two concepts (`add` for append and for sum) | Split the vocabulary |
| Disambiguation by number (`process1`, `process2`) | Name the difference |
| Names that need a comment to be understood | The comment is the name |

Booleans read as predicates: `is_ready`, `has_credit`, `should_retry`.
Functions that return nothing read as commands: `save_invoice`, `flush_queue`.

## Functions

- Target under 20 lines; anything over 40 is a defect, not a preference.
- Extract until the body reads as a paragraph of named steps.
- Parameter count: 0 best, 1-2 fine, 3 suspicious, 4+ requires a parameter
  object or a split.
- Output parameters (mutating an argument to return data) are banned. Return
  a value.
- A flag parameter means the function has two behaviours. Split it into two
  named functions and let the caller choose.
- Keep one level of abstraction per function: do not mix `calculate_tax()`
  with `cursor.execute(...)` in the same body.
- The function name plus its parameters should predict the return value with
  no surprises. No hidden writes, no hidden network calls.

## Control flow

- Guard clauses first, happy path last and unindented.
- Nesting depth over 3 means an extraction is overdue.
- Replace a type switch that repeats across the codebase with polymorphism or
  a dispatch table. A switch that appears exactly once can stay.
- Negative conditions cost a mental step: prefer `if is_valid` to
  `if not is_invalid`.
- Loops that build and filter and transform at once: split into named steps
  or use the language's pipeline constructs.

## Duplication

- Two occurrences: leave it, note it.
- Three occurrences: extract, only if all three encode the *same decision*.
- Identical shape with different intent is not duplication. Merging it creates
  a false abstraction that will need a flag parameter within a month, and that
  is strictly worse than the copies.
- Prefer extracting a function over introducing inheritance.

## Comments

Keep:
- Why a non-obvious choice was made, especially performance or compatibility.
- Links to a spec, ticket, or upstream bug that explains a workaround.
- Warnings about consequences (`not thread-safe`, `O(n^2) by design, n < 50`).
- Public API documentation.

Delete:
- Restatement of the code in prose.
- Change logs, author tags, dated banners: version control owns these.
- Commented-out code.
- Markers with no owner and no ticket reference. Either file it or fix it.

## Errors and boundaries

- Raise exceptions instead of returning status codes.
- Define error types by what the caller can do about them, not by where they
  were thrown.
- Never return `null`/`None` to mean "nothing happened" when an empty
  collection or an explicit result type is available.
- Validate at the boundary, then trust the core. Do not re-check the same
  invariant in every layer.
- Wrap third-party APIs behind an interface you own. Keep vendor types out of
  your domain code so a vendor change touches one file.
- Log at the boundary, not at every level: one exception, one log line.

## State and side effects

- Push I/O, clocks, randomness and mutation to the edges; keep the core
  deterministic so it is testable without mocks.
- Avoid temporal coupling (`init()` then `run()` then `close()`). If order is
  mandatory, enforce it in the type or with a context manager.
- Prefer immutable values for anything that crosses a function boundary.
- Global mutable state is a defect with a delay fuse.

## Tests

- One concept under test per test function.
- Arrange / act / assert, visibly separated.
- Test names describe behaviour and condition:
  `returns_empty_list_when_no_matches`.
- No logic (loops, conditionals) inside a test body.
- No shared mutable fixtures between tests.
- If a test needs five mocks, the design is the problem, not the test.

## When not to refactor

Refuse, and say why, when:

- The code is stable, isolated, and nobody reads it. Ugly and untouched is
  cheaper than clean and re-broken.
- It is generated, vendored, or a database migration already applied.
- There are no tests, no time to write them, and the change is cosmetic.
- The refactor would change a published API or serialization format.
- The only justification is personal preference against an existing formatter
  or linter configuration.
