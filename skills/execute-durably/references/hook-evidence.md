# Selective hook evidence

The plugin hook observes `apply_patch`, `Edit`, and `Write` after they complete. It appends a per-session, hash-chained JSONL ledger under `PLUGIN_DATA`, `COGNITIVE_POWERS_DATA`, or `~/.codex/cognitive-powers`, in that order. It does not observe every possible write path, run tests, block tools, or replace durable criteria.

At `Stop`, a warning means the latest recorded edit is not covered by a current receipt. First produce a real JSON command receipt with `work_state.py run` or `run-green`, then confirm that criterion through `work_state.py verify`. Record the hook receipt only after that independent confirmation:

```powershell
& $python <plugin-root>/hooks/selective_hooks.py record-validation --session-id <hook-session-id> --data-root <hook-data-root> --evidence <command-receipt.json> --validator <different-agent-id>
```

The `Stop` warning reports both identifiers. The command requires schema version 1, a successful `command` or `test_cycle` receipt stored in the matching durable session, argv, output hashes, a current source fingerprint, and a criterion whose active state is independently verified by the same validator identifier. The identifier check is a guardrail, not proof of cognitive independence. Use a genuinely fresh verifier before treating the warning as resolved.

Hook failures remain fail-open so observability cannot disable Codex editing. A clean hook warning state is supporting evidence only; `complete` still requires current criteria and independent verification through `work_state.py`.
