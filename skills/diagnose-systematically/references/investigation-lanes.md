# Adaptive investigation lanes

Use parallel read-only investigation only when independent perspectives can reduce real uncertainty. Run `investigation_protocol.py route` with normalized signals when routing will be reused or evaluated.

## Lanes

- `reproduction-scope`: establish the narrowest reliable trigger and impact boundary.
- `code-path-failure-seam`: trace state and control flow to the first observable divergence.
- `recent-change-regression`: compare nearby history, contracts, flags, schemas, and dependencies.
- `proof-observability`: identify the smallest non-mutating command or existing evidence that distinguishes the leading hypotheses.

Give every lane the same symptom packet and only its assigned question. Keep lanes read-only. Require `hypothesis_key`, hypothesis, distinguishing prediction, falsifier, current evidence, missing evidence, smallest proof step, and confidence.

Do not parallelize a small reproduced defect with one likely seam. Do not launch lanes merely to reach a fixed reviewer count. The main agent owns reproduction, synthesis, instrumentation, fixes, and final claims.

## Synthesis

Normalize lane findings through `investigation_protocol.py synthesize`. Reuse one `hypothesis_key` for the same causal theory across lanes. The script merges exact keys, preserves the lowest confidence reported, and ranks evidence-backed theories ahead of unsupported guesses.

The normalized output sets `root_cause_proven=false`. A ranked hypothesis becomes a supported cause only after its discriminating prediction is observed through the symptom-specific signal.
