# Context economy

Read this reference for large, unfamiliar, or noisy repositories.

## Context Lens

Use the deterministic selector to rank likely relevant text without loading the whole tree:

```powershell
& $python scripts/context_lens.py <root> --query "exact error symbol behavior" --max-files 12 --max-chars 12000
```

Set `$python` to a real Python 3 executable first. In Codex desktop, prefer the bundled path returned by the workspace dependency loader and verify `& $python --version`; do not install Python merely because the Microsoft Store alias fails.

Add `--json` when another script will consume the output. The reported character reduction compares extracted payload text with all scanned text; it is not an actual model-token measurement.

The tool ignores common generated, dependency, cache, and VCS directories. It caps file size and output payload, and emits matching excerpts with line numbers. Ranking is a search aid only. Before editing, read each selected file completely and inspect its callers, dependencies, and tests as needed.

## Typed context pipeline

Use `scripts/context_pipeline.py` when context comes from several adapters or must pass through normalization before one shared budget is applied. The library keeps the stages explicit:

1. A `ContextProvider` returns attributable `ContextItem` values.
2. Zero or more `ContextProcessor` implementations normalize or filter them without changing their identities.
3. A `ContextSelector` applies a `ContextBudget` and returns both selected items and a decision for every processed item.

The bundled `RankedBudgetSelector` uses bounded lexical relevance plus optional integer priority. It fills `max_chars` and `max_items`, truncates at most the final fitting item, and excludes the remainder. It is a deterministic fallback, not semantic relevance proof.

Each run emits a `ContextReceipt` containing provider, processors, selector, budget, selected size, and an inclusion/exclusion/truncation decision with reason and SHA-256 for every original item. A processor-filtered item remains visible in the receipt. Preserve the receipt with any conclusion that depends on the packet.

Call `receipt.mark_consumed(...)` only for selected items actually used by the downstream analysis. Then call `lint_context(...)` over the original candidates and receipt. The lint reports:

- `duplicate`: normalized contents are identical.
- `contradiction`: one declared `metadata.fact_key` has multiple `fact_value` values.
- `stale`: `metadata.valid_until` is earlier than the lint time.
- `unconsumed`: an included or truncated item was never marked consumed.

These checks find packet defects. They do not prove completeness, truth, semantic relevance, or an end-to-end model improvement.

## Skill routing regression

After changing skill frontmatter, run:

```powershell
& $python <plugin-root>/scripts/run_skill_routing_benchmarks.py
```

The suite reads the installed skill descriptions and checks realistic positives, negative prompts with an owning skill, adversarial prompts, rank-1/top-k rates, and pairwise description collisions. Every active skill must have cases. Thresholds are checked-in regression floors derived from the current catalog, not universal quality targets. The report always keeps `end_to_end_improvement_proven` false.

## High-value sequence

1. Search exact errors, symbols, filenames, or identifiers.
2. Inspect matches and their nearest behavioral tests.
3. Follow imports and callers only across the affected boundary.
4. Run a targeted check to resolve the largest remaining uncertainty.
5. Expand to broader checks after the targeted behavior succeeds.

## External documentation boundary

Context Lens selects repository text only. When a decision depends on a versioned external API, invoke `$use-current-docs` after finding the dependency and its version locally. Query one concrete topic, cap the returned payload, and verify the resulting implementation against project tests or runtime behavior.

## Common sources of waste

- Reading a whole repository before forming a query.
- Reopening unchanged files or repeating identical searches.
- Loading generated output, vendored dependencies, or large logs without filtering.
- Delegating overlapping tasks that duplicate the same context.
- Writing long plans for direct work.
- Treating more context as a substitute for executable evidence.
