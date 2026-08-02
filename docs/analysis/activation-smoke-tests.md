# Activation smoke tests

Fifteen prompts to run by hand in a fresh session. They check something the gate cannot: whether a
workflow actually activates for a request phrased the way requests arrive, rather than whether the
deterministic router ranks it first.

Those are different claims. `scripts/run_skill_routing_benchmarks.py` scores `skill_routing.decide`,
which is one input to the session; what the model does with the session-start index, the standing
instruction, and the router's suggestion is not decidable offline. Every prompt below already routes
correctly through `decide` — that was measured while writing them. What is unmeasured is the step
after.

## How to run

Start a session with no prior context (a genuine startup, not a resume: the session-start injection
deliberately skips a resumed session, whose history already holds it). Paste one prompt. Record what
happened before answering anything else, then start another fresh session for the next one. Do not
run them in sequence in one session — after the first, the transcript itself becomes context and the
test stops being about activation.

For each prompt, record three things:

1. Did the response name a workflow at all?
2. Was it the expected one?
3. Did it actually follow the workflow, or only mention it? A named skill that changes nothing about
   the work is a mention, not an activation, and counts as a failure here.

Point 3 is the one worth guarding. Invoking the skill tool is a proxy for the workflow shaping the
answer, not proof of it.

## Should trigger

| # | Prompt | Expected workflow |
|---|---|---|
| 1 | the login button does nothing when I click it, no idea why | `diagnose-systematically` |
| 2 | can you double check that what you just did actually works | `verify-delivery` |
| 3 | this module is a mess, tidy it up without changing behaviour | `refactor-cleanly` |
| 4 | how do I use the new stripe api in this project | `use-current-docs` |
| 5 | explain this paper to me, I don't follow the maths | `eli5` |
| 6 | walk me through this repo, I've never seen it before | `map-project` |
| 7 | the checkout flow is broken in the browser, check it end to end | `verify-web-behavior` |
| 8 | this is going to take a while and I'll lose context, keep track of it | `execute-durably` |
| 9 | make the landing page look less generic | `design-intentionally` |
| 10 | compare these two approaches with a proper experiment | `research-systematically` |

Prompt 2 needs a preceding turn to be about anything real, or there is nothing to double-check;
give it one trivial change first and then send it.

Prompts 7 and 9 will report a missing provider on a repository without Playwright or a web
interface. That is a pass for this test: the question is whether the workflow activated, not
whether the provider was present.

## Should stay quiet

No workflow should be named for any of these. A named workflow here is a worse failure than a
missed one above: noise on ordinary work is what teaches the agent to stop reading the channel.

| # | Prompt | Why it must stay quiet |
|---|---|---|
| 11 | what time is it in Tokyo | no engineering content at all |
| 12 | write me a haiku about coffee | no engineering content at all |
| 13 | rename this variable from x to count | a mechanical one-file edit |
| 14 | change the button color to blue | sits beside `design-intentionally` and is not a design decision |
| 15 | what does this repo do | sits beside `map-project` and asks for an answer, not stored memory |

Prompts 14 and 15 are the sharp ones. Both are near misses by construction, and both are the
territory the trigger vocabulary was written to stay out of.

The standing instruction is expected to appear for all five. That is not a failure: it asks the
agent to check and to say in one clause that nothing applied. What fails here is naming a workflow
or running one.

## Recording a result

A run is worth keeping only if it records the phrasing verbatim, since a paraphrase changes the
thing under test. Note the session source (startup, clear, or compact), because the index is not
injected on a resume, and a prompt tested there is testing the router alone.

Deterministic baseline at the time of writing, over prompts 1-10 and the five controls: ten of ten
named the right workflow and none of the controls drew a suggestion. Before the description rewrite
the same prompts scored three of ten with two misroutes.
