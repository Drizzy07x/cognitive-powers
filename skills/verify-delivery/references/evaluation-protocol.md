# Plugin evaluation protocol

Use this protocol before claiming Cognitive Powers improves Codex quality or token use.

## Conditions

Compare fresh, isolated runs with the same model, reasoning effort, prompt, tools, fixture, and permissions:

- A: Codex base.
- B: Codex with Cognitive Powers.
- C: a comparable specialized plugin, when one exists.

For adaptive communication, add a terse control when practical:

- A: Codex base.
- B: Codex base with only a short instruction such as `Answer concisely.`
- C: Cognitive Powers with `communicate-efficiently`.

This separates generic brevity from the value of profile selection and evidence preservation.

Randomize A/B order and repeat each task at least three times. Keep development cases separate from held-out cases. The evaluator and hidden checks must be outside the agent's writable fixture.

## Score quality before efficiency

Measure objective behavior and hidden tests, instruction compliance, verification quality, preservation of unrelated state, and final-answer accuracy. Treat fabricated evidence, hidden-test modification, out-of-scope writes, or a false completion claim as critical failures.

Record strict success rate, quality score, critical failures, first-pass success, routing precision/recall, changed files, tool calls, duplicate reads, duration, and tokens.

For cumulative rollout token events, use only the final cumulative value. Report total tokens and fresh input tokens separately:

```text
fresh_input_tokens = input_tokens - cached_input_tokens
```

Compare token use only among paired successful runs, while still reporting every failed run. A cheap incorrect response is not an efficiency win.

When provider usage is available, normalize it with `communicate-efficiently` receipts. Keep input, cached input, fresh input, output, and total tokens separate. Never derive the missing arm from an assumed savings ratio.

## Minimum claim thresholds

Before stating "better quality and fewer tokens", require all of the following on the versioned suite:

- Zero critical failures.
- Strict success rate no lower than the baseline.
- Mean quality at least 5 points higher on a 100-point rubric.
- Median total tokens at least 15% lower on paired successful runs.
- Median fresh input tokens at least 20% lower.
- No more than 5% token overhead on tasks where the plugin should abstain.

Static manifest checks, skill validation, and Context Lens benchmarks validate mechanics only. They do not establish an end-to-end quality improvement.
