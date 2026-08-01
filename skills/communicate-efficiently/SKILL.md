---
name: communicate-efficiently
description: Select and apply the shortest reporting profile, compact, normal, or explicit, that still preserves evidence in a progress update, answer, or handoff. Compresses presentation, never evidence.
when_to_use: Use when the user explicitly asks for adaptive brevity or a set verbosity, when a handoff is consequential enough that profile choice is genuinely ambiguous, or when a reusable communication receipt is required. Skip routine progress notes and completion reports.
---

# Communicate Efficiently

Use the shortest profile that preserves the user's ability to understand, verify, and act on the result. Compress presentation, never evidence.

## Select a profile

- `compact`: routine progress, simple answers, and low-risk handoffs. Lead with outcome; include only changed state, decisive evidence, and the next blocker.
- `normal`: diagnoses, design choices, multiple material changes, or results with limitations. Preserve the causal chain and enough context to evaluate it.
- `explicit`: irreversible operations, ambiguous ordering, consequential decisions, or instructions where omitted conjunctions could change meaning. Use complete sentences and ordered steps.

Run `scripts/communication_contract.py select --input <signals.json>` when profile selection will be reused or benchmarked. Read [communication-contract.md](references/communication-contract.md) for its schema and precedence.

## Compose

1. State the outcome first.
2. Include every material result, failure, limitation, and unverified surface once.
3. Preserve commands, paths, identifiers, API names, versions, hashes, error strings, and quoted user requirements exactly.
4. Omit greetings, self-congratulation, repeated plans, routine tool narration, and a second summary of the same facts.
5. Keep code, commit text, release notes, and externally required formats in their native style.

Do not use deliberately broken grammar or remove words whose absence makes scope, causality, negation, sequence, or uncertainty harder to read. Preserve the user's language.

## Measure honestly

Use `scripts/communication_contract.py receipt` only with usage produced by the provider or execution harness. Never estimate a counterfactual baseline. Use `compare` only for the same task and only when both variants succeeded without critical failures, and never across providers: `compare` refuses two receipts whose recorded schemas differ, because they do not count a cached prompt the same way.

When the host does not hand you a usage record directly, derive one from its own transcript with `scripts/communication_contract.py usage-from-transcript --transcript <path>`. Hooks receive that path. Read [communication-contract.md](references/communication-contract.md) before relying on the result.

Use `assess` for deterministic contract checks such as required facts, exact literals, prohibited filler, and a case-specific word ceiling. Passing this check establishes presentation-contract compliance only; it does not establish improved model quality or lower end-to-end token use.

For a product-level efficiency claim, invoke `verify-delivery` and follow its paired evaluation protocol. Quality and task success take precedence over brevity.

## Hand off

Report the result, exact checks and outcomes, and remaining limitations. Do not expose internal reasoning or a chronological diary. Expand immediately if the user asks for detail or the compact form creates ambiguity.

## Pause points

DO-CONFIRM: work from judgment, then stop at each point and confirm every item. An unconfirmed item goes in the report, never silently past it.

**Before composing**
- Profile chosen by stakes and audience, not by habit.
- Everything compression may drop is presentation, never evidence.

**Before handing off**
- Exact checks and results survive at every profile.
- A receipt is claimed only when it is provider-backed.
