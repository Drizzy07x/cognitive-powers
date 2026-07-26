# Optional semantic navigation

Use semantic navigation only with an existing, fresh index. Installing or initializing CodeGraph or Graphify is a user decision; never do either implicitly.

Refreshing an index that already exists is different from creating one. On Claude Code the plugin runs `graphify update` at session start only when Graphify is installed, the working directory is a checkout, and `graphify-out/` is already present, so an existing index tracks the worktree without being asked. A checkout with no index is left untouched, which is what keeps the plugin from dropping a generated tree into a repository you do not own. The refresh is skipped when Git reports the worktree unchanged since the last run. Set `COGNITIVE_POWERS_DISABLE_INDEX=1` to turn it off entirely.

The provider-neutral adapter chooses a usable CodeGraph index, then a fresh Graphify index bound to the exact worktree, then deterministic lexical navigation:

```powershell
& $python <skill-root>/scripts/semantic_provider.py --root <repo> probe
& $python <skill-root>/scripts/semantic_provider.py --root <repo> search --query "<flow question>"
& $python <skill-root>/scripts/semantic_provider.py --root <repo> affected --file <changed-file>
```

Graphify freshness requires `graphify-out/graph.json`, `manifest.json`, an exact `.graphify_root` binding, and matching source hashes. A missing binding, changed/missing source, invalid schema, or different worktree forces lexical fallback. Provider confidence is normalized to `high`, `medium`, `low`, or `unknown` while preserving the raw label.

Completeness is judged by Graphify's own incremental detector when the provider is importable, and otherwise from the manifest. The reported `detector` names which one answered, and `completeness_verified` says whether coverage was actually established. The manifest detector reports `unverified` rather than `complete`: it can prove every indexed file is current, but it cannot see a file Graphify has never indexed, because Graphify's corpus rules are not derivable from its output. Results then carry `coverage_verified: false`, and absence from the candidate list is not evidence of absence. The session-start refresh is what closes that gap, so a long session that adds files should not assume the graph covers them.

## Route the question

Prefer CodeGraph for relational questions:

- how one symbol, request, event, or route reaches another;
- callers, callees, implementations, and cross-file dependencies;
- likely blast radius before changing a public symbol;
- candidate tests affected by changed source files;
- structural orientation in a large or tangled repository.

Prefer Context Lens or targeted search for exact strings, configuration keys, prose, assets, unsupported languages, and focused work where the relevant file is already known.

## Use the available surface

Prefer the installed CodeGraph MCP `explore` tool when present. Otherwise run the deterministic adapter:

```powershell
& $python <skill-root>/scripts/semantic_context.py --root <repo> --json probe
& $python <skill-root>/scripts/semantic_context.py --root <repo> --json explore --query "<flow question>" --max-files 8 --max-chars 12000
& $python <skill-root>/scripts/semantic_context.py --root <repo> --json impact --symbol <symbol> --depth 2
& $python <skill-root>/scripts/semantic_context.py --root <repo> --json affected --file <changed-file>
```

The adapter uses CodeGraph only when its status reports an initialized, complete index with no pending source changes, unresolved references, reindex recommendation, or worktree mismatch. Otherwise it returns a labeled Context Lens fallback.

## Bound trust

Treat every returned item as a candidate for navigation, never behavioral proof. The normalized response says `proof_status: navigation_only` and `requires_behavioral_verification: true`; run selected tests and inspect relevant runtime behavior before claiming correctness.

After edits, heed CodeGraph's staleness banner. Read only the named pending files directly; continue using fresh graph results for unaffected files. If the whole index is unavailable or frozen, stop querying it for that project and use built-in tools.

Do not repeat a graph result with broad grep or duplicate file reads. Open a complete file before editing when the returned excerpt does not establish its local invariants or user-owned surrounding changes.
