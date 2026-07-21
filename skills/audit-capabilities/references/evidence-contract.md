# Capability audit evidence contract

The assessor accepts one UTF-8 JSON object:

```json
{
  "schema_version": 1,
  "patterns": [
    {
      "id": "durable-release-check",
      "summary": "Repeat the same release verification sequence",
      "candidate_name": "verify-release",
      "closest_skill": "verify-delivery",
      "occurrences": [
        {
          "event_id": "release-2026-06",
          "source": "rollout_summaries/release-june.jsonl",
          "observed_at": "2026-06-10"
        },
        {
          "event_id": "release-2026-07",
          "source": "sessions/release-july.jsonl",
          "observed_at": "2026-07-12"
        }
      ],
      "repository_paths": ["scripts/release_check.py"],
      "triggers": ["verify this release"],
      "workflow_steps": ["inspect artifacts", "run release checks"],
      "validation_commands": ["py -3 scripts/release_check.py"]
    }
  ]
}
```

## Field rules

- `id`: stable identifier unique within the packet.
- `summary`: the repeated procedure, not merely its subject.
- `candidate_name`: lowercase hyphen-case proposed name.
- `closest_skill`: existing local skill name when one covers any meaningful portion; otherwise `null`. The assessor verifies named skills.
- `occurrences`: historical observations. Use one canonical `event_id` for every representation of the same underlying task. `source` identifies where the observation came from and `observed_at` uses `YYYY-MM-DD`.
- `repository_paths`: relative, non-traversing paths that currently support relevance. The assessor checks them against the checkout.
- `triggers`: concrete user requests that should invoke the capability.
- `workflow_steps`: reusable procedural steps.
- `validation_commands`: real project checks the capability should prescribe. They are audit data and are not executed by the assessor.

At least two distinct event IDs establish recurrence. Evidence remains current when a declared repository path exists or an event falls within the configured staleness window. Missing paths never count as current evidence.

Memory index entries and their linked rollout summaries frequently describe the same task. Give them the same event ID. Artificially splitting one task into several events invalidates the audit.

An event is one independently authorized task execution with its own outcome or validation boundary. Follow-up prompts, retries, subagents, memory entries, and rollout records remain part of the parent event. Separate requests count separately only when each initiated and completed an independent workflow; turn count alone does not establish separate events.

Pass `--evidence -` to read the same JSON object from stdin when the audit must remain strictly read-only. The assessor does not persist stdin content.

## Output limits

`priority_score` ranks supported candidates using recurrence, current paths, concrete triggers, workflow steps, and validation commands. It does not measure model quality, implementation effort, or expected return on investment.

`review-overlap` means lexical similarity found a plausible existing home even though `closest_skill` was `null`. Inspect that skill before changing the candidate to `new` or `update`.
