---
name: research-systematically
description: Run reproducible research: freeze a pre-registration of question, hypotheses, falsifiers, and stopping rules, run labeled confirmatory and exploratory experiments, then bind every claim to evidence.
when_to_use: Use for comparative evaluations, benchmarks, or experimental investigations where exploration must not be presented as confirmation. For a bounded factual lookup about an external library, use use-current-docs instead.
---

# Research Systematically

Turn an uncertain question into a reproducible research record without presenting exploration as confirmation.

## 1. Freeze the question

Write the question, hypotheses, predictions, falsifiers, methods, experiments, and stopping rules before collecting result evidence. Normalize that packet with `scripts/research_protocol.py preregister`; retain its hash unchanged.

## 2. Run bounded experiments

Label every experiment `confirmatory` or `exploratory`. A confirmatory result must correspond to the frozen registration. New probes are exploratory even when they produce a better result. Record deviations, failed approaches, and pivots instead of rewriting the original plan.

## 3. Bind claims to evidence

Give every evidence item a source and content fingerprint. Every material claim, experiment result, dead end, and pivot must reference evidence IDs. Absence of evidence is a gap, not a negative result.

## 4. Verify independently

Ask a verifier who did not produce the research result to inspect the frozen plan, evidence, deviations, and claims. Normalize the result with `scripts/research_protocol.py evaluate`. An inconclusive verdict cannot complete the research.

## 5. Report honestly

Separate confirmed results, exploratory observations, rejected hypotheses, dead ends, pivots, deviations, and unanswered questions. Bind the report to the pre-registration and evidence hashes. Read [protocol.md](references/protocol.md) for the packet contract and examples.
