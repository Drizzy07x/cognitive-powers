# Extraction matrix

Book-derived operational principles mapped to checkable workflow rules for
1.8.0. Sources are named for provenance; every rule below is written in this
repository's own words and phrased so an agent can pass or fail it against an
observable artifact. Two proposed additions were homed into existing workflows
instead of duplicated: the smell catalog joins `refactor-cleanly` and the
ordered debugging rules join `diagnose-systematically`, because the routing
gate holds description collisions at zero and a sibling with the same
vocabulary would steal its twin's prompts rather than add a capability.

| # | Source | Principle | Checkable rule | Target | Kind |
| --- | --- | --- | --- | --- | --- |
| 1 | A Philosophy of Software Design | Shallow module red flag | Flag a unit whose public surface rivals its body; report location, cost, fix | `design-review` | new |
| 2 | A Philosophy of Software Design | Information leakage | Flag one decision encoded in two or more places; name the single owner to create | `design-review` | new |
| 3 | A Philosophy of Software Design | Temporal decomposition | Flag structure that mirrors execution order while two steps share a format each half-knows | `design-review` | new |
| 4 | A Philosophy of Software Design | Pass-through methods | Flag a method that forwards its arguments unchanged and adds no contract | `design-review` | new |
| 5 | A Philosophy of Software Design | Conjoined methods | Flag two units that cannot be understood separately; merge or re-split | `design-review` | new |
| 6 | A Philosophy of Software Design | Comments that restate code | Flag a comment a rename would delete; keep only why-comments | `design-review` | new |
| 7 | A Philosophy of Software Design | Vague or unpickable names | Flag names that fit several meanings; a name that resists choosing is a design finding | `design-review` | new |
| 8 | A Philosophy of Software Design | Non-obvious code | Flag code whose reader needs a fact the code does not state | `design-review` | new |
| 9 | A Philosophy of Software Design | Strategic over tactical | Default to the design that makes the next change cheap; the tactical shortcut must be declared in the handoff | `solve-efficiently` | upgrade |
| 10 | A Philosophy of Software Design | Design it twice | A new public interface requires two sketched alternatives, and the handoff names the rejected one and why; skipping the second sketch is allowed only when one plausible shape exists and the handoff says so | `solve-efficiently` | upgrade |
| 11 | A Philosophy of Software Design | Deep modules | New public surface must hide more than it exposes; a wrapper that saves callers nothing is rejected | `solve-efficiently` | upgrade |
| 12 | Debugging (nine rules) | Understand the system | Name the involved components and their expected behavior before any theory | `diagnose-systematically` | upgrade |
| 13 | Debugging (nine rules) | Make it fail | A recorded reproduction command precedes any cause claim | `diagnose-systematically` | upgrade |
| 14 | Debugging (nine rules) | Look before theorizing | Captured actual state precedes each hypothesis; inference is not observation | `diagnose-systematically` | upgrade |
| 15 | Debugging (nine rules) | Divide and conquer | Each bisection step and its verdict is recorded | `diagnose-systematically` | upgrade |
| 16 | Debugging (nine rules) | One change at a time | One variable per experiment; a change that did not help is reverted before the next | `diagnose-systematically` | upgrade |
| 17 | Debugging (nine rules) | Keep an audit trail | Every attempt, exact change, and observed result is written down as it happens | `diagnose-systematically` | upgrade |
| 18 | Debugging (nine rules) | Check the plug | Environment assumptions (build, branch, config, versions) are verified before code is suspected | `diagnose-systematically` | upgrade |
| 19 | Debugging (nine rules) | Get a fresh view | A stalled investigation re-derives from evidence or hands the evidence to a fresh reader | `diagnose-systematically` | upgrade |
| 20 | Debugging (nine rules) | Prove the fix | The original reproduction passes after the fix and fails when the fix is reverted, where cheap to show | `diagnose-systematically` | upgrade |
| 21 | Working Effectively with Legacy Code | Change points | The exact locations a change must touch are listed before any edit | `legacy-safe-changes` | new |
| 22 | Working Effectively with Legacy Code | Seams | A sensing or substitution point is identified without editing the code under change | `legacy-safe-changes` | new |
| 23 | Working Effectively with Legacy Code | Dependency-breaking moves | Only named moves (sprout method, sprout class, wrap method, extract interface, parameterize constructor) break a dependency | `legacy-safe-changes` | new |
| 24 | Working Effectively with Legacy Code | Characterization tests | Tests record what the code does today, written fail-first and then pinned to observed output | `legacy-safe-changes` | new |
| 25 | Working Effectively with Legacy Code | Change inside the net | The requested change lands only after the net is green; preservation is re-verified after | `legacy-safe-changes` | new |
| 26 | Refactoring | Smell-to-move catalog | Each named smell maps to a named move; the report says which move fixed which smell | `refactor-cleanly` | upgrade |
| 27 | Refactoring | Small steps | One move at a time, checks re-run after each; a red check reverts the step | `refactor-cleanly` | upgrade |
| 28 | Code Complete | Pseudocode first | A non-trivial new routine starts as intent-level steps; surviving steps become its comments | `solve-efficiently` | upgrade |
| 29 | Code Complete | Construction checks | Naming, defensive programming, and error handling are explicit audit dimensions with observable pass conditions | `verify-delivery` | upgrade |
| 30 | Code Complete | Review pass | The diff is re-read line by line as a distinct pass after writing | `verify-delivery` | upgrade |
| 31 | The Pragmatic Programmer | Single authority | Before adding a constant, format, or rule, search for the existing owner; two authorities is a finding | `execute-durably` | upgrade |
| 32 | The Pragmatic Programmer | Orthogonality | A diff serves one concern; unrelated-module edits split into separate packets | `execute-durably` | upgrade |
| 33 | The Pragmatic Programmer | Crash early | An impossible state raises a domain error at detection, never limps forward | `execute-durably` | upgrade |
| 34 | The Pragmatic Programmer | Suspect own code first | A framework-bug claim requires a minimal reproduction outside the project | `execute-durably` | upgrade |
| 35 | The Pragmatic Programmer | Tracer bullets | Multi-criterion work lands a thin end-to-end slice first; the first receipt proves the wiring | `execute-durably` | upgrade |
| 36 | The Checklist Manifesto | Pause points | Every workflow carries DO-CONFIRM checklists at fixed pause points, ten items or fewer, killer items only | all workflows | format |

## Format layer contract

The pause-point sections added to every workflow follow one shape: the agent
works from its own judgment, then stops at the named point and confirms each
item; an unconfirmed item is reported, never silently skipped. No checklist
exceeds ten items, and an item earns its place only when missing it is both
silent and expensive.
