# Evidence standard

Use the strongest practical evidence required by each claim.

## Evidence levels

1. **Inspection**: confirms text, structure, configuration, or a static relationship.
2. **Static analysis**: confirms a formatter, linter, type checker, parser, or validator accepted the artifact.
3. **Build**: confirms compilation or packaging for the tested target.
4. **Behavioral test**: confirms specified behavior for exercised cases.
5. **Runtime validation**: confirms startup or behavior in the real application or representative environment.
6. **External state**: confirms current remote, deployed, scheduled, published, or service state.

Higher levels do not automatically cover unrelated lower-level claims. A clean build does not prove startup, and a unit test does not prove remote deployment.

## Claim record

For each material claim, record:

- Claim.
- Required evidence level.
- Evidence obtained, including command or source.
- Result and exit status where applicable.
- Verdict: verified, partially verified, unverified, or contradicted.

## Useful-test gate

A test contributes evidence only when it observes requested behavior or a realistic failure boundary. For a regression test, confirm that it fails against the defective state when practical. Do not count a test that merely searches for an implementation string, always passes, silently skips, or never reaches the changed behavior.

If a check cannot run, report the exact reason. Never replace execution with an invented or predicted result.
