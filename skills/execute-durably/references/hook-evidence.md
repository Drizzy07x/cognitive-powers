# Selective hook evidence

The plugin hook observes the host's file-writing tools after they complete: `apply_patch`, `Edit`, and `Write` on Codex, and `Edit`, `Write`, and `NotebookEdit` on Claude Code. It appends a per-session, hash-chained JSONL ledger under `COGNITIVE_POWERS_DATA`, `PLUGIN_DATA`, or `~/.codex/cognitive-powers`, in that order. It does not observe every possible write path, run tests, block tools, or replace durable criteria.

That root is shared with `work_state.py` by design, and both resolve it the same way on every host. A host-specific data variable is deliberately not consulted: Claude Code exports `CLAUDE_PLUGIN_DATA` to hook processes only, so honouring it here would point the hook at a root that no receipt writer can reach.

At `Stop`, a warning means the latest recorded edit is not covered by a current receipt. First produce a real JSON command receipt with `work_state.py run` or `run-green`, then confirm that criterion through `work_state.py verify`. Record the hook receipt only after that independent confirmation:

```powershell
& $python <plugin-root>/hooks/selective_hooks.py record-validation --session-id <hook-session-id> --data-root <hook-data-root> --evidence <command-receipt.json> --validator <different-agent-id>
```

The `Stop` warning reports both identifiers. The command requires schema version 1, a successful `command` or `test_cycle` receipt stored in the matching durable session, argv, output hashes, a current source fingerprint, and a criterion whose active state is independently verified by the same validator identifier. The identifier check is a guardrail, not proof of cognitive independence. Use a genuinely fresh verifier before treating the warning as resolved.

Hook failures remain fail-open so observability cannot disable editing on any host. A clean hook warning state is supporting evidence only; `complete` still requires current criteria and independent verification through `work_state.py`.
