# Optional domain glossary

Create `CONTEXT.md` only when at least one project-specific term has a resolved meaning that future tasks cannot cheaply infer. Do not create it merely because a repository lacks one.

## Content boundary

Record domain language and relationships:

- canonical term;
- concise meaning in the problem domain;
- distinctions from easily confused terms;
- concrete edge case when needed to make the distinction stable.

Exclude file paths, commands, frameworks, implementation details, task plans, and coding rules. Those belong in the host's instruction file, source, or a specification.

## Multiple domains

Use a root `CONTEXT-MAP.md` only when the repository contains distinct domains whose vocabularies would conflict or burden unrelated tasks. Point to one context file per genuine domain instead of duplicating a global glossary.

## Decisions

Offer an architectural decision record only when the choice is costly to reverse, surprising without its rationale, and the result of a real tradeoff. Keep the glossary about meaning; keep implementation decisions in ADRs.
