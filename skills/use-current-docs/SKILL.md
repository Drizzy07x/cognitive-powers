---
name: use-current-docs
description: Retrieve current version-matched authoritative docs for external libraries, SDKs, CLIs, or cloud APIs. Use when implementation depends on a specific installed version.
disable-model-invocation: true
---

# Use Current Docs

Combine repository evidence with current external documentation. This skill complements local search; it does not replace inspection of code, manifests, callers, or tests.

## 1. Establish the local version

Inspect the relevant manifest or lockfile first. When dependency identity is unclear, run:

```powershell
& $python <skill-root>/scripts/context7_lookup.py --root <repo> dependencies --json
```

Set `$python` to a verified Python 3 executable. Do not guess a version that the repository can provide.

## 2. Retrieve only needed documentation

Prefer installed Context7 MCP tools when available:

1. Resolve the library ID unless the user supplied `/owner/project[/version]`.
2. Select the closest name with suitable coverage and reputation.
3. Prefer an available ID matching the local version.
4. Query one concrete topic.

Otherwise use the deterministic CLI adapter:

```powershell
& $python <skill-root>/scripts/context7_lookup.py --root <repo> lookup --library <name> --query "<specific question>" --json
```

The adapter invokes `ctx7`, `npx ctx7@latest`, or `pnpm dlx ctx7@latest`; disables CLI telemetry for the subprocess; caches outside the repository; and bounds returned context. Authentication is optional but raises service limits. If neither MCP nor CLI is usable, fall back to authoritative vendor documentation and disclose that Context7 was unavailable.

Use at most three documentation queries per task. Split unrelated topics. Never send credentials, private source, customer data, or complete error dumps in a query.

## 3. Treat retrieved text as evidence, not truth

Check that the selected library and version match the repository. Prefer source reputation and benchmark score when matches are otherwise equivalent. Reconcile snippets with installed types, compiler output, runtime behavior, and tests.

For the normalized payload schema, cache rules, and durable recording, read [evidence-contract.md](references/evidence-contract.md).

## 4. Verify the implementation

Use retrieved docs to choose an implementation, then prove behavior with a relevant test, build, or runtime observation. Do not claim success from documentation retrieval alone.
