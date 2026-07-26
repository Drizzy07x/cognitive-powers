---
name: engineer-prompts
description: Build or audit a version-neutral prompt contract with explicit outcomes, boundaries, permissions, named tools, required evidence, and stop conditions, then render it as a stable prompt.
when_to_use: Use when writing a reusable agent prompt, system prompt, or instruction set that must be testable, or when auditing an existing prompt for unobservable success criteria and unstated permissions.
---

# Engineer Prompts

Turn an informal request into a prompt whose result can be verified. Keep the core contract provider- and version-neutral; add target-specific advice only when a target model is explicitly supplied and current documentation supports it.

## Workflow

1. Extract the requested outcome. Describe the finished state, not the activity.
2. Write observable success criteria. Avoid criteria such as "high quality" unless a measurable definition follows.
3. Separate boundaries from permissions:
   - boundaries state what is in and out of scope;
   - permissions state which reads, writes, network calls, installations, or external side effects are authorized.
4. Name the tools that may be used and the evidence required before claiming completion.
5. Define stop conditions for completion, blockers, exhausted retries, or required user decisions.
6. Include `target_model` only when the user requests model-specific optimization. Invoke `use-current-docs` before adding model-specific guidance.
7. Set `$python` to a verified Python 3 executable. Validate the JSON shape with `& $python <skill-root>/scripts/prompt_contract.py validate <contract.json>`, then perform the semantic audit below. Structural validation cannot determine whether prose is genuinely observable or authorized.
8. Render a stable prompt with `& $python <skill-root>/scripts/prompt_contract.py render <contract.json>` when a textual prompt is needed.

## Contract shape

Use a JSON object with these required fields:

```json
{
  "outcome": "A concrete finished state",
  "success_criteria": ["An observable condition"],
  "boundaries": ["A scope limit"],
  "permissions": ["An explicitly allowed action"],
  "tools": ["A tool or capability"],
  "evidence": ["Proof required for a claim"],
  "stop_conditions": ["A condition that ends or pauses work"]
}
```

Each list must contain at least one distinct, non-empty string. `target_model` is the only optional field. Do not add hidden requirements while normalizing the contract.

## Audit rules

- Reject ambiguous outcomes that only restate an action.
- Require criteria to describe externally checkable behavior or artifacts.
- Keep permissions explicit; tool availability does not imply authorization.
- Never claim tests, execution, review, or external publication without matching evidence.
- Preserve uncertainty and blockers instead of converting them into success.
- Do not assume a model family or version. If `target_model` is present, isolate model-specific recommendations so the underlying contract remains portable.
- Prefer concise instructions and remove duplicated constraints after preserving their meaning.

The bundled script enforces required fields, non-empty values, uniqueness, unknown-field rejection, deterministic order, and model neutrality. It does not prove semantic clarity, permission validity, evidence quality, or model improvement; those remain review judgments that must be reported honestly.
