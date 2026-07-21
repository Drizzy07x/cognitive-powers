# Adaptive Review Angles

Choose review depth from consequence and change surface, not a fixed reviewer count.

Use `scripts/review_protocol.py select` with a normalized JSON packet when selection will be benchmarked or reused. The output is a routing decision, not evidence that any pass ran.

## Focused work

Use one independent verification pass with separate Contract and Quality verdicts, covering the original contract, relevant diff, and exact behavioral check. Add another angle only when the first pass exposes a distinct unresolved risk.

## Cross-cutting or release-critical work

Use two or three independent passes selected from:

- Contract and scope: every requested outcome, prohibited side effect, and preserved user change.
- Runtime and QA: behavior at the affected happy path, boundary, and failure path.
- Code and diff: affected callers, state transitions, compatibility, and unintended edits.
- Project context: repository conventions, prior decisions, release state, and authoritative history.
- Durable evidence: receipt structure, source fingerprint, artifact hash, executor/verifier separation, and stale-state detection.

Security is a review angle only when explicitly requested or when the changed behavior crosses an authentication, authorization, secret, untrusted-input, or destructive-operation boundary.

## Verdict contract

Each pass returns one verdict:

- `confirmed`: current direct evidence supports every mandatory claim in its scope.
- `failed`: evidence contradicts at least one mandatory claim.
- `inconclusive`: a required surface could not be tested or evidence is missing, stale, skipped, or ambiguous.

Only `confirmed` can contribute to completion. Never average away a failed or inconclusive mandatory check.
