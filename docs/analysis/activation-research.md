# Skill activation research

Input for the skill-activation work. It records what two external skill collections do to make
skills fire, what this repository will take from them, and what it will refuse to take and why.

Mechanisms below are described in our own words. No prose, prompt, or code was copied from either
source. References use the form `repo · path:line` and point into clones kept outside this tree at
`C:\dev\reference\`; they are never committed here.

## 1. The defect this work targets

`benchmarks/skill_routing_cases.json` reports a healthy surface: `rank1=1.00`, `top-k=1.00`,
`suggested=0.98`, `spanish=0.95`, `quiet=1.00`, `collisions=0`, `misrouted=0`. That measurement
cannot see the problem, and `CLAUDE.md` already says why: its prompts were written against the
skill descriptions, so a skill that never fires on natural phrasing is invisible to it by
construction.

Measuring the same `skill_routing.decide()` against prompts written independently of the
descriptions gives a different number:

| Prompt (natural phrasing) | Expected | Decision |
|---|---|---|
| the login button does nothing when I click it, no idea why | `diagnose-systematically` | silent |
| can you double check that what you just did actually works | `verify-delivery` | `execute-durably` |
| this module is a mess, tidy it up without changing behaviour | `refactor-cleanly` | silent |
| how do I use the new stripe api in this project | `use-current-docs` | correct |
| explain this paper to me, I don't follow the maths | `eli5` | correct |
| walk me through this repo, I've never seen it before | `map-project` | silent |
| the checkout flow is broken in the browser, check it end to end | `verify-web-behavior` | correct |
| this is going to take a while and I'll lose context, keep track of it | `execute-durably` | silent |
| make the landing page look less generic | `design-intentionally` | `legacy-safe-changes` |
| compare these two approaches with a proper experiment | `research-systematically` | silent |

Fired on 5 of 10, named the right workflow on 3 of 10. Five silences and two thefts between
siblings. A five-prompt off-domain control (`what time is it in Tokyo`, `write me a haiku about
coffee`, `what's the capital of Peru`, `rename this variable from x to count`, `git status`) drew
no suggestion at all.

Two facts follow. Under-triggering, not noise, is the failure mode. And there is headroom to
broaden, but the size of that headroom is unmeasured outside these five controls.

## 2. Source A — `obra/superpowers`

Recorded in `integrations/catalog.json` as `kind: pattern`, `status: approved`,
`decision: adapt-pattern`. That decision already permits taking the idea and not the
implementation, which is exactly the boundary this work needs.

### 2.1 Session-start injection

One hook, registered for `SessionStart` with matcher `startup|clear|compact`
(`superpowers · hooks/hooks.json:5`). `resume` is excluded deliberately: a resumed session already
carries the text in its history, and re-injecting it was a reported defect
(`superpowers · RELEASE-NOTES.md:348`).

The payload is not generated. The hook reads one static skill file in full and wraps it in a fixed
template (`superpowers · hooks/session-start:11,27`), delivering it to Claude Code as
`hookSpecificOutput.additionalContext` (`superpowers · hooks/session-start:41-43`). Measured live:
3,051 characters of skill body plus 226 characters of wrapper, 3,276 total, about **819 tokens**.

What it does **not** do matters more than what it does. It does not walk the skills tree, does not
enumerate the other thirteen skills, and builds no index. The injected text is behavioural: a rule
that a skill must be invoked whenever there is even a marginal chance it applies, plus a table of
rationalisations to check against before acting
(`superpowers · skills/using-superpowers/SKILL.md:11-16,37-50`).

Failure is silent at two layers: no interpreter found exits zero rather than erroring
(`superpowers · hooks/run-hook.cmd:36-39`), and an unreadable skill file degrades into fallback
text rather than crashing (`superpowers · hooks/session-start:11`).

### 2.2 Description style

Two frontmatter fields only, `name` and `description`. Thirteen of fourteen descriptions open with
the literal words `Use when`, followed by enumerated triggering conditions. Lengths run 79–234
characters, mean about 133.

The authoring rule is stated at `superpowers · skills/writing-skills/SKILL.md:95-104,140-197`: a
description declares **when to use the skill and nothing else**, never a summary of what it does.
That file records the incident behind the rule — a description that summarised the workflow led the
agent to skip a required review stage. Keyword coverage is a separate rule
(`superpowers · skills/writing-skills/SKILL.md:199-205`): include literal error strings, symptom
words, and synonyms.

None of the fourteen descriptions quotes a literal sentence a user would type.

### 2.3 Discovery

There is none. No search, index, ranking, or dispatch code exists in that repository. Discovery
rests on the host preloading every skill's name and description, and their porting guide states
plainly that this offers no structural guarantee the trigger fires
(`superpowers · docs/porting-to-a-new-harness.md:549-551`). The forced session-start injection is
the compensation for that gap.

## 3. Source B — `daymade/claude-code-skills`

### 3.1 Description rules

The description is treated as the primary triggering mechanism, and under-triggering is named
explicitly as the observed model behaviour, with a prescription to write descriptions that push
harder than feels natural (`daymade · daymade-skill/skill-creator/SKILL.md:388-390`).

Enforced and advisory limits:

- 1,024 characters maximum, enforced in code
  (`daymade · daymade-skill/skill-creator/scripts/quick_validate.py:342-353`).
- Under 50 characters is flagged as unlikely to trigger
  (`daymade · daymade-skill/skill-reviewer/scripts/review_skill.py:231-232`).
- A keyword-presence check requires the description to contain one of a small set of trigger
  phrasings (`daymade · daymade-skill/skill-reviewer/scripts/review_skill.py:234-237`). It is a
  proxy for stating trigger conditions, not a measurement of whether the skill fires.
- Third person is required by the review checklist, with worked corrections
  (`daymade · daymade-skill/skill-reviewer/references/evaluation_checklist.md:14,21-27`).
- Generalise from failing queries into broader categories of intent rather than accumulating a list
  of specific queries (`daymade · daymade-skill/skill-creator/scripts/improve_description.py:127-130`).

That repository contradicts itself on grammatical person: its generator prompt asks for the
imperative (`daymade · daymade-skill/skill-creator/scripts/improve_description.py:135`) while its
reviewer marks that same construction as a defect. Nothing there resolves it. We follow third
person, which is also what our existing corpus uses.

### 3.2 Trigger evaluation

A real harness exists, and it measures firing rather than ranking. For each candidate query it
registers the skill under test, spawns a headless provider run, and watches the streamed tool
events for an invocation referencing that skill
(`daymade · daymade-skill/skill-creator/scripts/run_eval.py:70-91,128-168`). Each query runs
several times, and a trigger rate at or above a threshold counts as a pass
(`daymade · daymade-skill/skill-creator/scripts/run_eval.py:230-234`). An optimisation loop
proposes new descriptions from the failures and picks the winner on a held-out split rather than on
the training queries.

The recorded failure modes of that harness are the most useful part for us:

- An optimiser can converge on the unmodified original because every iteration triggered on nothing
  and the score was meaningless
  (`daymade · daymade-skill/skill-creator/references/skill-development-methodology.md:356-364`).
- A probe that stops at the first skill invocation can be fooled by a hook injecting a different
  skill first, reporting a false negative
  (`daymade · .../references/skill-development-methodology.md:377-385`). This one bears directly on
  the hooks added by this work.
- A skill being invoked is a proxy for its content shaping the answer, not proof of it.
- Some tasks are low-threshold enough that the model simply does them with a basic tool, and no
  description rewrite changes that (`daymade · daymade-skill/skill-creator/SKILL.md:1078-1086`).

## 4. What this repository ports

1. **A standing instruction, not a longer catalogue.** The evidence from source A is that firing
   improves because the session is told to check for a matching skill before acting, not because a
   list was supplied. The host already preloads every name and description.
2. **Session-start matcher that excludes resume**, for the reason source A recorded: the text is
   already in a resumed session's history.
3. **Trigger-condition-led descriptions**: state when to use the skill, enumerate concrete
   conditions, and cover the vocabulary a user in that situation actually reaches for — symptoms,
   synonyms, and error wording rather than quoted sentences.
4. **Generalise, do not enumerate queries.** A description tuned to specific failing prompts overfits
   and stops generalising.
5. **Silent degradation in advisory hooks.** Already this project's rule for the advisory shape; the
   external precedent confirms it rather than changing it.
6. **Trigger rate as a distinct measurement from ranking.** Our benchmark scores which skill wins.
   Whether any skill fires on natural phrasing is a different question and needs its own corpus.
   This belongs to the follow-on eval-harness work.

## 5. What this repository refuses to port

1. **The "supersedes X" tiebreaker clause.** Source B prescribes resolving ambiguity by naming the
   competing skill inside the description
   (`daymade · daymade-skill/skill-creator/SKILL.md:339-343`). Our router matches lexically, so a
   description that names a sibling adds that sibling's vocabulary to its own bag and wins prompts it
   meant to hand over. This is measured, not theoretical: `CLAUDE.md` records that two such clauses on
   `refactor-cleanly` cost one Spanish misroute and dropped Spanish routing to 0.92, and removing them
   returned zero misroutes at 0.94. Separation between siblings belongs in the skill body, which the
   router never scores.
2. **Injecting a full skill body at session start.** 819 tokens buys one skill's discipline text
   there. We have nineteen workflows and a hook budget that has to stay defensible.
3. **The imperative voice** of source B's generator prompt. Its own reviewer rejects it, and our
   corpus is third person already.
4. **Their evaluation implementation.** It spawns a provider. Our gate runs offline by contract, so a
   trigger-rate harness has to be built to that constraint or stay outside the gate, like the two
   runners already excluded from it.

## 6. Constraints that bound any description rewrite

The routing benchmark is not advisory. Current values against their floors:

| Metric | Floor | Current | Headroom |
|---|---|---|---|
| `min_quiet_rate` | 1.00 | 1.00 | **none** — 50 off-domain prompts, zero tolerance |
| `min_spanish_rate` | 0.93 | 0.95 | 0.02 |
| `min_suggestion_rate` | 0.85 | 0.98 | 0.13 |
| `min_rank1_rate` | 0.70 | 1.00 | 0.30 |
| `min_top_k_rate` | 0.95 | 1.00 | 0.05 |
| `min_negative_rate` | 0.90 | 1.00 | 0.10 |
| `min_adversarial_rate` | 0.90 | 1.00 | 0.10 |
| `max_collision_similarity` | 0.72 | 0 collisions | — |
| `max_misrouted` | 3 | 0 | 3 |

The host truncates `description` plus `when_to_use` at 1,536 characters. The current corpus runs
363–781 combined, so length is not the binding constraint; vocabulary overlap is.

The intended change — broadening trigger vocabulary — pushes directly against `min_quiet_rate`,
which has no headroom, and against collision similarity between siblings that describe adjacent
work. Every rewrite has to be re-measured. `SPANISH_TERMS` in `scripts/skill_routing.py` is a
carrier: a Spanish case whose content words the lexicon cannot translate ranks first yet draws no
suggestion, and the 0.93 floor fails the benchmark on it.
