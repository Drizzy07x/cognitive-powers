---
name: design-review
description: Review code structure against named depth red flags - shallow modules, information leakage, temporal decomposition, pass-through methods, conjoined functions, comments that restate the code, vague names, and non-obvious control flow - reporting each finding with its location, its comprehension cost, and the named fix. Use when the task involves judging a module boundary, an interface that feels wider than what it hides, or a structural pass over code just written or about to be merged.
when_to_use: Use when asked whether a module is too shallow, an interface too wide, a name too vague to pick one meaning, an abstraction is leaking its internals across files, or the same decision is encoded twice. Also run it on a new public interface before it ships. Findings only; it changes no code.
---

# Design Review

Judge structure by one currency: what it costs the next reader to change the
code safely. Every flag below names an observable symptom, why it taxes that
reader, and the move that removes it. Report findings; do not apply fixes.
Structural fixes belong to `refactor-cleanly`, visual and interface direction
to `design-intentionally`, and completion audits to `verify-delivery`.

## Locate plugin files

Paths written as `<plugin-root>/...` are relative to the installed Cognitive
Powers root: the directory that contains `skills/`, `scripts/`, and a
`.codex-plugin/plugin.json` or `.claude-plugin/plugin.json` manifest. Resolve
them from this skill's own location rather than guessing, and never copy plugin
scripts into the target repository.

## 1. Bound the review

Name the modules in scope and read each one completely, interface first, then
body. Run `<plugin-root>/hooks/clean_code_guard.py --scan <path>` for the
mechanical floor - length, nesting, parameters. The flags below are what the
scanner cannot see; do not repeat its findings as your own.

## 2. Sweep the red flags

For every flag report the location, the symptom observed, and the named fix.
A sweep that finds nothing states which flags were checked and where.

| Flag | Detect | Why it costs | Fix |
| --- | --- | --- | --- |
| Shallow module | Public surface rivals body size; a wrapper that saves callers nothing | Interface must be learned without buying anything | Deepen: pull real work behind the interface, or inline the wrapper away |
| Information leakage | One decision - a format, a protocol, a path rule - encoded in two or more places | Every change must find all copies or corrupt one | Give the decision one owner module; the others call it |
| Temporal decomposition | Structure mirrors execution order; step modules share a format each half-knows | Knowledge splits across files that must change together | Reorganize around who knows what, not what runs when |
| Pass-through method | Forwards its arguments unchanged and adds no contract, check, or translation | A layer to read that answers no question | Remove the layer, or make it earn its keep with a real contract |
| Conjoined functions | Neither of two units can be understood without the other open | Two names, one tangled meaning | Merge them, or re-split along a boundary each side can state alone |
| Comment restates code | Deleting the comment loses nothing a rename would not restore | Reader verifies two copies of one fact | Fix the name; keep only comments that carry why |
| Vague name | `data`, `info`, `handle`, `process`, or a name fitting several meanings | Every call site must be read to learn what the name refused to say | Rename for one meaning; a name that resists choosing is itself a finding - restructure until it can be named |
| Non-obvious code | The reader needs a fact the code does not state: an ordering, a unit, an invariant | Correct-looking edits break the unstated rule | Make the fact visible in code, or state it where it is depended on |

## 3. Judge depth

For each public interface in scope, weigh what a caller must learn against what
the module does for them. Flag any unit where learning the interface costs more
than writing the body inline would. Prefer recommending one deeper module over
several shallow ones; do not recommend splitting a unit merely for size when
the pieces would share hidden state.

## 4. Report

Order findings by comprehension cost, most expensive first. Each carries
`file:line`, the flag name, the observed symptom, and the named fix. Close with
what was reviewed and found clean, so a silent miss is distinguishable from an
unchecked file. Never rewrite the code from inside the review.

## Pause points

DO-CONFIRM: work from judgment, then stop at each point and confirm every
item. An unconfirmed item goes in the report, never silently past it.

**Before sweeping**
- Scope named as an explicit file list, not a directory guess.
- Each in-scope module read whole, interface before body.
- Mechanical scan run, so its findings are not resold as judgment.

**Before reporting**
- Every finding carries location, flag name, symptom, and named fix.
- Findings ordered by comprehension cost, not by file order.
- Clean files named as checked, so absence of findings is evidence.
- No code was changed anywhere in the review.
