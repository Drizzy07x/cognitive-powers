# Durable agent roles

Use the smallest set of roles needed by the packet graph. Custom agent TOML files are optional; the role contracts still apply when delegating to built-in workers.

## Executor

Give the executor exactly one packet, its owned paths, dependencies, invariants, and declared checks. It may modify only owned paths. Require exact commands and observed results, and stop if another ownership boundary is needed.

## Test writer

Use a separate test writer only when a real pre-fix failure can be demonstrated without overlapping the executor's active ownership. Require the same focused command for RED and GREEN. A syntax error, broken fixture, or invented output is not RED evidence. If separation would create overlapping edits, keep the test and implementation with one executor and preserve the recorded red/green cycle instead.

## Verifier

Give a fresh verifier the objective, relevant diff, criterion, and receipts without an expected verdict. Require read-only inspection and a `confirmed`, `rejected`, or `inconclusive` result grounded in current evidence. A different identifier does not provide independence when the verifier has been shown the desired conclusion.

Repository-scoped `.codex/agents/*.toml` may encode these roles for development. Installed plugins do not currently register custom agents from the plugin root, so never make durable execution depend on those files being discoverable.
