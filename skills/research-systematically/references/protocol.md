# Research protocol

## Pre-registration

Use schema version 1 with a stable `research_id`, one question, hypotheses with predictions and falsifiers, methods, planned experiments, and stopping rules. Run:

```powershell
& $python <skill-root>/scripts/research_protocol.py preregister --input <plan.json> --json
```

Store the returned object and `preregistration_hash`. Do not replace the hash after results are known.

## Experiment classes

- `confirmatory`: declared before results; its ID and mode must match the registration.
- `exploratory`: added to understand an unexpected result or discover a new direction. It can motivate later confirmatory research but cannot retroactively become confirmatory.

Each reported experiment has a status, result, evidence IDs, and explicit deviations. Completed and deliberately stopped experiments are closed; other statuses remain open.

## Evidence and claims

Evidence entries require an ID, source, SHA-256 content fingerprint, and observation. Claims cannot exist without at least one valid evidence reference. A reference proves traceability, not that the interpretation is correct; the verifier decides whether the evidence supports the claim.

Dead ends record the attempted approach and why it failed. Pivots record the prior approach, new approach, reason, and evidence. Neither should be erased from the closeout.

## Independent verdict

The verifier records identity, rationale, evidence IDs, and one verdict:

- `confirmed`
- `partially-confirmed`
- `rejected`
- `inconclusive`

Run:

```powershell
& $python <skill-root>/scripts/research_protocol.py evaluate --input <results.json> --json
```

Research is complete only when every planned confirmatory experiment is reported, no experiment remains open, and the verifier is not inconclusive. Completion does not authorize implementation or external publication.
