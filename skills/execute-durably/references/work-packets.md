# Durable work packets

Use work packets only after synthesizing one dependency-aware plan for a medium or large task. Packets coordinate implementation; they never replace session criteria, integrated evidence, or independent verification.

## Plan schema

For a human-authored plan, compile strict Markdown before installing state:

```powershell
& $python <skill-root>/scripts/plan_compiler.py compile --input <plan.md> --output <plan.json>
& $python <skill-root>/scripts/work_state.py --root <repo> plan-packets --session <id> --plan <plan.json>
```

Each `## Packet: <id>` requires `### Objective`, `### Owned paths`, `### Dependencies`, `### Invariants` (or `### Acceptance criteria`), `### Checks`, and `### Integration notes`. List every check as a backtick-wrapped JSON argv array, for example ``- `["py", "-3", "-m", "unittest"]` ``. Use one `none` dependency bullet when a packet has no dependencies. Compilation is fail-closed and writes output atomically.

```json
{
  "schema_version": 1,
  "packets": [
    {
      "id": "parser",
      "objective": "Update the parser without changing its public API",
      "owned_paths": ["src/parser", "tests/test_parser.py"],
      "dependencies": [],
      "invariants": ["Existing CLI inputs remain compatible"],
      "checks": [["py", "-3", "-m", "unittest", "tests.test_parser"]],
      "integration_notes": ["Run the full suite after dependent packets"]
    }
  ]
}
```

Paths are workspace-relative ownership boundaries. The planner rejects duplicate IDs, unknown or cyclic dependencies, unsafe paths, empty checks, and ancestor/descendant ownership overlap across packets.

## Lifecycle

```text
planned -> active -> completed
             |
             +-- check pending -> in_progress -> passed
                                      |
                                      +-> failed -> retry
```

Install the complete plan atomically:

```powershell
& $python <skill-root>/scripts/work_state.py --root <repo> plan-packets --session <id> --plan <plan.json>
```

Use `--plan -` for stdin. Start only packets whose dependencies are completed and whose receipts remain valid:

```powershell
& $python <skill-root>/scripts/work_state.py --root <repo> start-packet --session <id> --packet <packet-id> --owner <agent-id>
& $python <skill-root>/scripts/work_state.py --root <repo> run-packet-check --session <id> --packet <packet-id> --check k1 --executor <agent-id>
& $python <skill-root>/scripts/work_state.py --root <repo> complete-packet --session <id> --packet <packet-id> --actor <agent-id>
```

`run-packet-check` executes the declared argv without a shell; callers cannot substitute another command. A passed check is bound to hashes of the packet's owned paths, so independent packets can change their own files without invalidating it. Later changes inside the completed packet's ownership make its receipt invalid.

Completing the last packet leaves the session active. Run an integrated criterion command and obtain independent verification before `complete` can close the session.
