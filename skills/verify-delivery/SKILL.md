---
name: verify-delivery
description: Audit an already-completed implementation, diagnosis, research delivery, release, or benchmark against real evidence, separating what the evidence supports from what was only asserted.
when_to_use: Use when asked to review a completion claim, check tests and Git state, judge release readiness, or find unsupported claims. Never use while implementing a change; this workflow audits finished work only.
disallowed-tools: Edit, Write, NotebookEdit
---

# Verify Delivery

Treat completion as a set of falsifiable claims, not a confident summary.

For a bounded checkout with one explicit claim document and a small declared evidence surface, use a focused audit instead of the remaining broad workflow: one batch for the claim, status, exact local/tracking/remote identity, relevant diff/artifact evidence, and named tests; then run the single focused suite once. The Construction checks below stay in force for a code delivery - the bound narrows what is read, not what a diff owes. Do not load references, inspect unclaimed signing/identity/history, try alternate test frameworks, or run `knowledge_closeout.py` for that bounded case.

## Locate plugin files

Paths written as `scripts/<file>` are relative to this skill's own directory.
Paths written as `<plugin-root>/...` are relative to the installed Cognitive
Powers root: the directory that contains `skills/`, `scripts/`, and a
`.codex-plugin/plugin.json` or `.claude-plugin/plugin.json` manifest. Resolve
both from this skill's own location rather than guessing, and never copy plugin
scripts into the target repository.

## 1. Freeze the contract

Extract the requested outcomes, constraints, authorized side effects, and promised verification. Separate explicit requirements from optional improvements. If reviewing code, record the initial repository state and preserve unrelated user changes.

## 2. Inventory the claims

List each material claim made or implied by the delivery, such as:

- A requested behavior exists.
- A defect's stated cause is supported.
- Tests, builds, or runtime checks passed.
- No out-of-scope files changed.
- Local and remote state match.
- A measured quality or token improvement is real.

Map each claim to evidence before deciding whether the whole delivery is complete.

Classify each claim on one of two axes:

- **Contract**: requested behavior, scope, constraints, authorized side effects, and promised verification.
- **Quality**: repository conventions, affected boundaries, maintainability, compatibility, and evidence integrity.

Read [two-axis-review.md](references/two-axis-review.md) for a code diff, cross-cutting change, or release-critical audit. Do not let strength on one axis offset a failure on the other.

## 3. Gather independent evidence

Prefer observable behavior and authoritative state over prose. Inspect the diff and relevant source, but run meaningful checks whenever execution is possible. A useful test must fail for the defect or missing behavior it is meant to detect, then pass for the implemented result.

Classify every claim using [evidence-standard.md](references/evidence-standard.md):

- `verified`: direct current evidence supports it.
- `partially verified`: some required surface remains untested.
- `unverified`: no adequate evidence was obtained.
- `contradicted`: current evidence disproves it.

Missing tools, skipped checks, timeouts, stale reports, or absent logs are never a pass.

For browser-visible claims, invoke `verify-web-behavior`. Require a parsed Playwright report with at least one relevant expected test and zero unexpected results. Report flaky retries separately; a screenshot or trace alone is supporting evidence, not a pass.

Treat `explore-web-adaptively` receipts, Skyvern completion status, extracted output, recordings, and generated Playwright candidates as navigation evidence. They may explain or discover a flow but cannot verify its requested outcome.

For design claims, require the current `design-intentionally` intent, dimension-matched viewport renders, explicit review checks, and relevant Playwright results. Treat `visualContractPassed` as review-contract evidence only. It does not prove subjective quality, cross-browser rendering, performance, accessibility, or behavior that was not asserted.

When changed files cross module boundaries and CodeGraph is already indexed and fresh, use `solve-efficiently`'s semantic-navigation workflow to obtain candidate affected tests and callers. Run those tests and inspect the relevant consumers; an empty affected set is not proof that no regression exists.

## 4. Test the boundaries

Check the nearest failure modes, not only the happy path. For code changes, consider inputs at boundaries, errors, state transitions, compatibility, and affected callers. For diagnosis, seek evidence that distinguishes the proposed cause from plausible alternatives. For Git or release state, compare the actual local, tracked, untracked, and remote surfaces in scope.

Use [evaluation-protocol.md](references/evaluation-protocol.md) when comparing Cognitive Powers against an unassisted host baseline or another plugin. Do not infer model-quality improvement from static validation or a single successful task, and do not carry a measurement from one host over to the other.

For communication-efficiency claims, inspect `communicate-efficiently` usage receipts. Reject estimated counterfactuals, mismatched task IDs, unsuccessful variants, critical failures, and comparisons whose candidate quality is lower.

For cross-cutting, release-critical, or independently delegated work, select the smallest useful set of perspectives from [review-angles.md](references/review-angles.md). Keep contract evidence out of the quality review and quality preferences out of the contract verdict. Do not force a security review unless the request or changed behavior materially includes a security boundary.

When several passes report findings, normalize them through [finding-contract.md](references/finding-contract.md) and `scripts/review_protocol.py synthesize`. Bind every pass and finding to the same source identity, merge only identical issue keys, preserve disagreements as conflicts, and order material findings by severity and confidence.

## Construction checks

For a code delivery, audit these four dimensions inside the Quality axis. Each has an observable fail condition; cite the line for every finding.

- **Naming.** Every identifier the diff introduces or repurposes says what it now holds or does. A name the change made misleading fails even though no line containing it changed.
- **Defensive programming.** Input crossing a trust boundary is validated where it enters. Impossibilities are asserted; expectable failures get errors. The distinction is checkable: an assertion that can fire on user input is a finding.
- **Error handling.** Every failure path the diff adds is handled or propagated with context, and the message names the failing thing and input. A newly introduced silent catch fails unless the surrounding component documents silence as its contract.
- **Review pass.** The whole diff was re-read line by line as its own step, distinct from writing it, and the report says so. Unrelated edits found in that pass are listed, not absorbed.

## 5. Render the verdict

Report the Contract and Quality verdicts separately. Within each axis, lead with its most important defect or verdict and cite the command, artifact, line, log, or state supporting each finding. State what passed, what failed, and what was not tested.

Bind the verdict to the exact source state and evidence artifact reviewed. If either changes afterward, the prior verdict is stale and must not authorize completion.

Declare completion only when every mandatory contract requirement is verified and the quality axis contains no completion-blocking contradiction. Never average the axes into a score. Otherwise return `failed` or `inconclusive`, a concrete gap list, and the smallest next check or fix needed.

## 6. Close the knowledge surfaces

After the delivery verdict, use `scripts/knowledge_closeout.py` to assess whether the result is reflected across six surfaces: code, tests, documentation, project guidance, release notes, and durable memory.

Use `light` mode for a narrow change and include only relevant surfaces. Use `full` mode for cross-cutting, release, architecture, or capability-lifecycle work and assess all six. A required surface is current only with evidence bound to the reviewed source identity.

The closeout is read-only. It may report cleanup or memory-write actions as authorized or blocked, but it never performs them. Do not clean artifacts or write durable memory unless the user explicitly authorized that separate side effect.

## Pause points

DO-CONFIRM: work from judgment, then stop at each point and confirm every item. An unconfirmed item goes in the verdict, never silently past it.

**Before gathering evidence**
- The contract frozen: outcomes, constraints, side effects, promised checks.
- Every material claim listed and mapped to the evidence that could falsify it.

**Before rendering the verdict**
- Each claim classified from current evidence; no stale report counted as a pass.
- Construction checks run on code deliveries, findings cited by line.
- Boundaries probed, not only the happy path.

**Before declaring complete**
- Contract and Quality verdicts separate; neither offsets the other.
- Verdict bound to the exact source state and artifacts reviewed.
- Gaps carry the smallest next check or fix, not a vague retry.
