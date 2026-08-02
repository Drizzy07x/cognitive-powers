# Skill activation mission

Branch `feature/skill-activation`, cut from `main` at 1.8.2. Not merged.

## The defect

Workflows were selected only when the agent happened to notice a match while reading a request.
`benchmarks/skill_routing_cases.json` could not see the problem, and `CLAUDE.md` already said why:
its prompts were written against the skill descriptions, so a workflow that never fires on natural
phrasing is invisible to it by construction.

Measured against ten prompts written independently of the descriptions, `skill_routing.decide`
named the right workflow **three times out of ten**, stayed silent five times, and misrouted twice.
One prompt shared no token with any workflow in the catalogue. A five-prompt off-domain control
drew nothing.

## What was implemented

**A session-start injection** (`hooks/skill_activation.py`, new). Renders the catalogue once per
session as one line per workflow plus a standing instruction to check it before acting. Registered
on `SessionStart` with matcher `startup|clear|compact` in both manifests, and gated again in code,
so a host that ignores the matcher still behaves. A resumed session is skipped: its history already
holds the text.

Rendering is a pure function of the catalogue (`index_message`), so the ceiling is testable with no
filesystem and no host. Measured payload: **2,969 characters, about 742 tokens**, all nineteen
workflows, against a declared ceiling of 3,200. The overflow path counts what it dropped rather than
listing part of the catalogue in the shape of all of it.

**A standing instruction on every prompt** (`hooks/skill_router.py`, extended). The hook now carries
two payloads instead of one. The named suggestion still fires only for a clear winner, because
naming a workflow for ordinary work is the noise that stops the channel being read. The standing
instruction makes no claim about the prompt and fires whenever the catalogue was readable:
**218 characters, about 54 tokens** alone, **560 characters, about 140 tokens** when a suggestion
rides with it. When the catalogue does not load, neither is injected — a standing order to consult
an index that is not there would be paid on every prompt.

It extends the existing hook rather than adding a second one on the same event. Two hooks writing
one event double the cost and can contradict each other.

**Shared host resolution** (`scripts/plugin_host.py`, new). Both hooks resolve the plugin root
through one lookup. Two hooks of one plugin resolving different installs in one session is the
condition `selective_hooks` names as fatal to the Stop gate.

**Trigger vocabulary in nine descriptions** plus two Codex routers, and two Spanish lexicon terms.
Not all nineteen — see the scope note below.

## Mechanisms ported, conceptually

From `obra/superpowers` (catalogued `pattern` / `approved` / `adapt-pattern`):

- Injecting behavioural instruction at session start rather than relying on passive description
  matching. Their own porting notes state there is no structural guarantee a description-based
  trigger fires.
- Excluding a resumed session from the injection, which they record as a fixed defect.
- Trigger-condition-led descriptions that state when a skill applies rather than what it does, and
  keyword coverage over symptom words and synonyms.
- Silent degradation everywhere in the hook path.

From `daymade/claude-code-skills` (registered this mission, `pattern` / `approved` /
`adapt-pattern`):

- Under-triggering named as the failure mode to design against, and descriptions written to push
  harder than feels natural.
- Generalise from failing prompts into categories of intent instead of accumulating specific
  queries.
- Third person, resolving that repository's own unresolved contradiction on grammatical person in
  favour of its reviewer.
- Trigger rate as a measurement distinct from ranking, with a held-out split, and its recorded
  harness failure modes — including that a probe stopping at the first skill invocation can be
  fooled by a hook injecting a different skill first. That one bears directly on the hooks added
  here.

Deliberately **not** ported: the tiebreaker clause that resolves ambiguity by naming the competing
skill inside a description. This router matches lexically, so naming a sibling hands it that
sibling's vocabulary. `CLAUDE.md` already records the measurement — two such clauses cost a Spanish
misroute and dropped Spanish routing to 0.92. Also refused: injecting a full skill body at session
start, and their provider-spawning evaluation implementation, which the offline gate cannot host.

Full reference detail, with file and line citations into the clones, is in
[activation-research.md](activation-research.md). The clones live outside the tree and are not
committed.

## Results

| Measurement | Before | After |
|---|---|---|
| Natural-phrasing prompts, right workflow named | 3 / 10 | **10 / 10** |
| Natural-phrasing prompts, misrouted | 2 | **0** |
| Off-domain controls drawing a suggestion | 0 / 5 | 0 / 5 |
| Benchmark rank-1 | 1.00 | 1.00 |
| Benchmark top-k | 1.00 | 1.00 |
| Benchmark suggestion rate | 0.98 | 0.98 |
| Benchmark Spanish | 0.95 | **1.00** |
| Benchmark off-domain silence | 1.00 | 1.00 |
| Benchmark misroutes | 0 | 0 |
| Negative-owner / adversarial-owner | 1.00 / 1.00 | 1.00 / 1.00 |
| Collisions | 0 | 0 |

Suite: 899 tests, all passing, 1 skipped. The skip count is PATH-dependent rather than
code-dependent — `InstallShTransactionTests` and `InstallerEquivalenceTests` are gated on `bash` and
`pwsh` being present, so the same tree reports 17 skips when the suite is launched from a shell that
cannot see `bash`. Gate: 25 commands, `offlinePassed: true`, no failed commands.

## Scope note: nine descriptions, not nineteen

The mission asked for all nineteen. Nine were rewritten. The other ten were measured first, with one
natural prompt each, and eight of the nine remaining workflows already named themselves correctly
with no edit at all; the ninth was fixed. Rewriting the rest would have been change without a
measured deficit, against a corpus where the off-domain floor has no tolerance — fifty near-miss
prompts must draw nothing, and several sit directly beside a workflow's territory. Every rewrite is
a risk to that floor, so the ones with nothing to fix were left alone.

That is a narrower change than requested, and it is deliberate rather than incidental. If the
intent was uniform authorship rather than measured activation, the remaining ten are a separate,
cheap pass — but they should be measured after, not assumed.

One trade is worth naming. Letting the cross-cutting workflow own work that spans layers returned
one benchmark positive to the silence 1.8.1 recorded as accepted, moving the suggestion rate from
1.00 back to 0.98. It buys the removal of a real misroute on natural phrasing. A misroute names the
wrong workflow; a silence names none.

## A finding that argues against part of the design

The session-start index duplicates work the host already does. Claude Code preloads every skill's
name and description into the system prompt at startup, which is why `superpowers` injects
behavioural instruction and no catalogue at all. The 742 tokens spent listing nineteen workflows are
therefore mostly redundant with something already present; the marginal value is in the standing
instruction, which is 54 of them.

The index was built as specified and is measured and bounded. Whether it earns its cost is exactly
the sort of question the follow-on eval harness can answer, by running the smoke prompts with the
index on and off.

## How to validate

```powershell
& $python scripts/run_skill_routing_benchmarks.py
& $python scripts/validate_skills.py --strict-quality
& $python -m unittest tests.test_skill_activation tests.test_skill_router
& $python scripts/validate_all.py --offline --json-output <path-outside-repo>.json
```

Then run [activation-smoke-tests.md](activation-smoke-tests.md) by hand. Nothing above tests whether
a workflow actually activates in a live session; that file is the only thing in this mission that
does, and it needs a person.

## Open items for the eval harness

1. **No corpus measures firing on natural phrasing.** The fifteen smoke prompts are hand-written and
   live in a document. They belong in a scored corpus with a floor, separate from the ranking
   benchmark, or the gap this mission closed can reopen without any check failing.
2. **Invocation is a proxy.** A workflow being named is not the workflow shaping the answer. The
   harness needs a signal for the second, or it will report activation that changed nothing.
3. **The hooks can fool a naive probe.** A probe that stops at the first skill invocation will read
   this plugin's own injection as the result. Any harness built here must account for its own
   instrumentation.
4. **The index has no measured benefit yet.** Run the smoke set with `COGNITIVE_POWERS_DISABLE_ACTIVATION`
   set and unset. If the difference is not visible, the 742 tokens should be cut to the standing
   instruction alone.
5. **Ten descriptions are unmeasured beyond one prompt each.** One natural prompt per workflow shows
   they are not broken; it does not show they are good.
6. **The off-domain floor is the binding constraint on everything above.** It is at 1.00 with a floor
   of 1.00 over fifty prompts. Any future vocabulary work needs that corpus grown, or it will be
   tuning against a measurement that cannot get worse without failing outright.
