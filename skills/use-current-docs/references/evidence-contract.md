# External documentation evidence contract

The lookup script emits schema version 1 with:

- provider, library name, selected library ID, requested and matched versions;
- exact query, retrieval and expiry timestamps;
- bounded code and prose snippets with available source metadata;
- SHA-256 of the untruncated provider response;
- local dependency evidence and cache status.

Cache files live under `COGNITIVE_POWERS_DATA`, or `~/.codex/cognitive-powers` when it is unset:

```text
<data-root>/external-context/<project-key>/<lookup-key>.json
```

The default TTL is 24 hours. `--refresh` bypasses a reusable entry. Cache identity includes the canonical repository path, library, requested version, library ID, query, provider, context budget, and requested TTL.

To bind a normalized result to durable work, record it as external context:

```powershell
& $python <execute-durably>/scripts/work_state.py --root <repo> record-context --session <id> --criterion <criterion> --executor <agent-id> --artifact <lookup.json> --summary "Version-matched documentation used"
```

The durable receipt copies and hashes the payload. It becomes stale when the repository source fingerprint changes, the copied payload changes, or its `expires_at` time passes. Independent verification and executable evidence remain required before completion.
