# Two-axis review

Evaluate the same source state through two independent questions.

## Contract axis

Use the original request, accepted clarifications, specification, and explicit prohibitions as the only requirements source. Check:

- every requested behavior and success criterion;
- missing or partial requirements;
- behavior or side effects outside the authorized scope;
- promised tests, runtime checks, publications, or Git operations;
- preservation of unrelated user work.

Do not invent quality preferences and present them as requirements.

## Quality axis

Use repository instructions, documented conventions, affected public interfaces, callers, tests, and evidence contracts. Check:

- correctness at relevant happy, boundary, and failure paths;
- compatibility and state transitions across affected callers;
- duplication, unnecessary indirection, speculative generality, and scattered changes when they materially raise maintenance cost;
- whether tests observe behavior at a suitable seam;
- whether receipts and reported results match what actually ran.

Treat undocumented style preferences as judgment calls, not hard failures. Skip checks already decided mechanically by a formatter or linter unless the tool failed.

## Independence and reporting

For focused work, one verifier may run both passes but must record separate verdicts. For cross-cutting or release-critical work, use fresh independent contexts when available and give each only the sources for its axis.

Return `verified`, `partially verified`, `unverified`, or `contradicted` for each axis. Keep findings grouped by axis and do not merge rankings or average results.
