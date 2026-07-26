---
name: map-project
description: Build or refresh compact hierarchical project memory, using whichever instruction filename the host reads, holding only facts that future tasks cannot cheaply infer from the tree.
when_to_use: Use when the user wants reusable repository guidance, when a large or unfamiliar tree has several genuinely distinct domains, or when existing project memory is stale. Skip during ordinary focused work unless project memory was requested.
---

# Map Project

Create durable project memory that contains only facts future tasks cannot cheaply infer. Do not run this workflow during ordinary focused work unless the user asks for project memory.

## 1. Resolve the instruction filename the host reads

Hosts do not agree on this filename, and a file the host never loads is wasted work. Determine the filename before writing anything:

- Claude Code reads `CLAUDE.md` and does not read `AGENTS.md`.
- Codex reads `AGENTS.md`.

Write the running host's filename. When the repository already carries the other host's file, do not duplicate its content: create the host's file importing the existing one, then add only host-specific guidance below it. In Claude Code that import is `@AGENTS.md` on its own line at the top of `CLAUDE.md`. Prefer the import over a symlink, which needs Administrator rights or Developer Mode on Windows.

Verify the result loaded rather than assuming it did.

## 2. Measure before writing

Set `$python` to a working Python 3 executable and verify it with `& $python --version` before running the script. On Windows the `python3` alias under `WindowsApps` is a Microsoft Store stub that exits without running Python; point at the real `python.exe` instead.

Run the project-map mode from Cognitive Powers' `solve-efficiently` skill:

```powershell
& $python <plugin-root>/skills/solve-efficiently/scripts/context_lens.py <repo-root> --project-map --max-depth 3 --json
```

Treat scores as candidates, not truth. Read every existing instruction file, under either host's filename, before editing. Inspect entry points, build/test configuration, module boundaries, and explicit project prohibitions. When CodeGraph is already indexed and fresh, use `solve-efficiently`'s semantic-navigation workflow to identify entry points, cross-directory dependencies, routes, and central public symbols. Otherwise use targeted search and Context Lens.

Read existing `CONTEXT.md`, `CONTEXT-MAP.md`, and architectural decision records when present. Keep domain language separate from operational agent guidance.

For an ambiguous large tree, delegate at most two independent read-only investigations: one for structure/entry points and one for conventions/tests. Verify their claims against files before writing.

## 3. Choose locations conservatively

Always consider the repository root. Add a child instruction file only when the directory is a distinct domain with guidance that would otherwise burden unrelated work. Preserve an existing child file even when its current score is low.

Both hosts load a child file only while working inside its directory, so guidance placed there is absent from an unrelated task by design. Never put a fact the root task needs into a child file.

Do not create files for generated output, dependencies, caches, or every directory. Parent guidance applies to descendants; child guidance must add or override something specific.

## 4. Write compact memory

Patch existing files instead of replacing them wholesale. Root guidance should normally stay within 40–120 lines and cover only useful sections:

- What the project does and its actual stack.
- Non-obvious structure and where to make common changes.
- Project-specific conventions and prohibited patterns.
- Exact build, test, and run commands verified from source.
- Important behavioral or tooling gotchas.

Child guidance should normally stay within 20–60 lines. Never repeat the parent. Omit generic engineering advice, decorative prose, generated timestamps, and claims not grounded in the checkout.

## 5. Capture domain language only when useful

Read [domain-glossary.md](references/domain-glossary.md) when repeated ambiguity in project-specific terms is making navigation, naming, or requirements harder. Create or update `CONTEXT.md` only for resolved domain meanings; do not turn it into another project map.

## 6. Verify the hierarchy

Confirm every referenced path and command exists, parent/child guidance does not conflict, and no child merely duplicates its parent. Report files created or updated, whether structural placement came from CodeGraph or lexical heuristics, and any centrality or runtime behavior that remained unmeasured.

State the filename written and the host that reads it. A file the running host does not load is not project memory, however well written.
