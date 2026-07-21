# Adaptive communication contract

## Selection input

`select` accepts a JSON object with these fields:

- `kind`: `progress`, `handoff`, `answer`, `diagnosis`, `decision`, or `warning`.
- `complexity`: `low`, `medium`, or `high`.
- `consequence`: `reversible`, `material`, or `irreversible`.
- `unresolved`: boolean.
- `evidence_count`: non-negative integer.

Precedence is `explicit`, then `normal`, then `compact`. Irreversible work and warnings are always explicit. High-complexity diagnoses, decisions, unresolved work, and reports with several evidence items are normal unless the explicit rule applies. Routine progress and low-risk handoffs are compact.

## Output assessment

`assess` accepts a case JSON object and either `--text` or `--text-file`. A case may define:

- `required_facts`: material phrases that must remain present, compared without case sensitivity.
- `exact_literals`: commands, paths, identifiers, errors, or hashes that must be byte-preserved.
- `forbidden_filler`: case-insensitive phrases that add no substance.
- `max_words`: a case-specific ceiling.

The result separates `integrityPassed`, `budgetPassed`, and `fillerPassed`. Overall `passed` requires all three. A ceiling is valid only for a prepared benchmark case; do not truncate a live answer to satisfy it.

## Usage receipts

`receipt` accepts a provider or harness JSON file containing a `usage` object with non-negative integer `input_tokens`, `cached_input_tokens`, and `output_tokens`. It records the source hash, model/provider metadata, task ID, variant, success, quality score, and critical-failure status. `fresh_input_tokens` is calculated as `input_tokens - cached_input_tokens`.

`compare` requires matching task IDs and refuses an efficiency verdict unless both runs succeeded, neither has a critical failure, and the candidate quality is no lower than the baseline. Report input, fresh-input, output, and total deltas separately.

These receipts prove what the supplied provider record contained. They do not prove that a missing baseline would have used a particular number of tokens.
