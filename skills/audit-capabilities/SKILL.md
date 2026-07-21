---
name: audit-capabilities
description: Audit recurring Codex workflows and existing skills to recommend evidence-backed updates or additions. Use for skill gaps, staleness, duplication, or capability reuse.
---

# Audit Capabilities

Recommend reusable capabilities from repeated procedures, not recurring topics.

## 1. Establish the current surface

Read the closest project instructions, README, validation commands, and existing skills under `skills`, `.agents/skills`, and `.codex/skills`. Read each relevant `SKILL.md` and `agents/openai.yaml` before proposing an overlap.

Treat the checkout as current authority. Memory can explain prior work but cannot override renamed paths, changed commands, or an existing skill that now covers the workflow.

## 2. Gather bounded historical evidence

Search the runtime memory summary first, then the memory index using the repository name, path, and important modules. Open only the one to three most relevant rollout summaries. Read raw session records only when a summary lacks an exact repeated procedure, validation command, or failure shield.

Assign the same `event_id` when a memory entry and rollout summary describe the same underlying event. Two representations of one event do not establish recurrence.

Normalize candidates using [evidence-contract.md](references/evidence-contract.md). Keep this audit packet outside the target repository unless the repository is an explicit benchmark fixture.

## 3. Assess candidates deterministically

Set `$python` to a verified Python 3 executable, then run:

```powershell
& $python <skill-root>/scripts/capability_audit.py assess --root <repo> --evidence <audit.json> --json
```

For a strictly read-only audit, pipe the normalized JSON through stdin instead of creating a packet:

```powershell
$auditJson | & $python <skill-root>/scripts/capability_audit.py assess --root <repo> --evidence - --json
```

The assessor rejects one-off and stale candidates, verifies declared repository paths, inventories local skills, forces a declared overlap to become an update, and holds lexically suspicious new skills for overlap review. Its ranking is a reproducible triage signal, not proof that a model will improve.

## 4. Interpret conservatively

- `update`: strengthen the named existing skill; do not create a duplicate.
- `new`: a repeated, current workflow remains distinct after the overlap scan.
- `review-overlap`: resolve likely duplication before recommending creation.
- `reject`: recurrence or current relevance is not established.

Prefer an update when triggers, paths, guardrails, validation, or UI metadata are the missing piece. Recommend a new skill only when the procedure has a separate trigger and coherent workflow.

Do not treat repository paths, validation commands, or topic similarity as recurrence. Do not count one incident twice through memory and rollout representations. Do not claim that a recommendation improves task success until a separate evaluation demonstrates it.

## 5. Report

Return the existing skills inspected, accepted updates, accepted new skills, rejected or unresolved candidates, evidence event count, decisive current paths, and priority order. State missing or stale evidence explicitly.

If the user authorizes implementation, invoke `$skill-creator` for the selected skill and add behavior-specific validation rather than continuing the audit.

## 6. Promote capabilities through evidence

Do not turn an audit recommendation directly into an active capability. Advance it through `observed → candidate → trial → active → retired` one state at a time with `scripts/capability_lifecycle.py`.

- `candidate` requires two distinct underlying events; duplicate memory and rollout representations still count once.
- `trial` requires source and implementation fingerprints plus passing, implementation-bound checks and evidence.
- `active` additionally requires explicit approval and a rollback plan targeting a different known fingerprint.
- `retired` requires an executed rollback with evidence.

The lifecycle script emits a hashed transition receipt. It does not install, activate, retire, or roll back a skill itself.

Every transition after the initial `observed` receipt must provide the immediately preceding receipt as `previous_receipt`. The script verifies its hash, capability identity, established state, next state, approved-transition marker, and chain fingerprint, then binds its fingerprint into the new receipt. For migration, an unchained schema-1 receipt may seed only the `observed → candidate` transition; later legacy receipts must be replayed from `observed` so an asserted `current_state` cannot bypass the lifecycle.
