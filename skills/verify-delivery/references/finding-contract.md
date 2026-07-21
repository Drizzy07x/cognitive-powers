# Review finding contract

Use structured findings when multiple review passes inspect the same source state.

Each finding contains:

- `finding_id`: identity of this report.
- `issue_key`: shared identity for the same issue across passes.
- `axis`: `contract` or `quality`.
- `category`: `regression`, `security`, `reliability`, `compatibility`, `coverage`, or `scope`.
- `severity`: `high`, `medium`, or `low`.
- `confidence`: `high`, `medium`, or `low`.
- `location`, `problem`, `evidence`, and `follow_up`.

Reuse `issue_key` only for the same observable problem. The synthesis script merges exact keys, keeps the highest reported severity and lowest reported confidence, and preserves all evidence and locations. Conflicting axes for one key are invalid.

Every pass also returns an axis verdict: `confirmed`, `failed`, or `inconclusive`. Missing, stale, or skipped evidence is `inconclusive`, never confirmed.

Security is a selected review angle only when requested or when authentication, authorization, secrets, untrusted input, permissions, or destructive behavior crosses the changed boundary. A concrete security defect discovered by another pass is still reportable.
