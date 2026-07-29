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

`receipt` accepts a provider or harness JSON file containing a `usage` object, in either of the two shapes the supported hosts produce. It records the source hash, model/provider metadata, task ID, variant, success, quality score, and critical-failure status, and names the shape it read in `usage.sourceSchema`.

- Codex: non-negative integer `input_tokens`, `cached_input_tokens`, and `output_tokens`. `input_tokens` already contains the cached prefix, so `cached_input_tokens` may not exceed it.
- Anthropic: `input_tokens`, `output_tokens`, `cache_read_input_tokens`, and optional `cache_creation_input_tokens`. Here `input_tokens` counts uncached input only, so total input is the sum of all three input fields and a cache read routinely dwarfs the uncached remainder.

The recorded `inputTokens` is total input under both shapes, and `freshInputTokens` is the part that was not read from cache; writing the cache counts as fresh. Treating the Anthropic field as a renamed `cached_input_tokens` would report a total that omits the cached prefix, so the conversion is not optional.

`compare` refuses two receipts whose `sourceSchema` differs, because the two providers do not count a cached prompt the same way and the delta would not measure anything.

`usage-from-transcript` builds an Anthropic record from a Claude Code transcript JSONL, whose path hooks receive as `transcript_path`. The host writes one row per content block and repeats the identical usage on each, so usage is taken once per `message.id`; summing rows would multiply a single message's cost. The record reports `messageCount` and `unparsableLines` so a caller can see how much it rests on. A transcript covering more than one model is refused.

This reads an on-disk format the host does not publish as an interface, so treat a schema change there as expected rather than exceptional. Two things make such a change visible instead of silent:

- Any assistant message whose usage is not fully readable makes the command refuse, naming the format as the suspect. A partially recognised row would otherwise undercount, and an undercount reads as a genuine efficiency result.
- `hostVersions` records the Claude Code build that wrote the counted rows, so a later discrepancy identifies which format produced the numbers rather than leaving it to be reconstructed.

Both the receipt writer and the durable recorder convert usage through `<plugin-root>/scripts/provider_usage.py`. The recorder re-derives receipt totals rather than trusting them, so reading fewer provider shapes than the writer accepts would reject correct evidence; one implementation removes that failure rather than detecting it afterwards.

`compare` requires matching task IDs and refuses an efficiency verdict unless both runs succeeded, neither has a critical failure, and the candidate quality is no lower than the baseline. Report input, fresh-input, output, and total deltas separately.

These receipts prove what the supplied provider record contained. They do not prove that a missing baseline would have used a particular number of tokens.
