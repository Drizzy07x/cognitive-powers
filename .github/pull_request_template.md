## What this changes

<!-- The behavior that differs, and why. Not a restatement of the diff. -->

## Evidence

<!--
Paste the summary line from the offline validation receipt:

  & $python scripts/validate_all.py --offline --json-output <path-outside-repo>.json

The receipt must land outside the repository and the worktree must be clean, or
the run fails closed. See CONTRIBUTING.md.
-->

- [ ] `validate_all.py --offline` passes locally
- [ ] Routing changes, if any, have cases in `benchmarks/skill_routing_cases.json`
- [ ] `CHANGELOG.md` has an entry under `## Unreleased`

## Anything a reviewer should look at first

<!-- Optional. The part you are least sure about is the most useful thing to name. -->
