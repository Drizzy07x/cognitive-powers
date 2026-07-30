# Contributing

Thanks for considering a contribution. This project gates everything on
reproducible evidence, so the fastest path to a merged change is to produce the
same receipt locally that CI will demand.

## Run the validation before opening a pull request

Install the exact, hash-pinned validation dependencies:

```powershell
& $python -m pip install --require-hashes -r requirements-dev.txt
```

Then run the canonical offline entrypoint. Two constraints trip people up, and
both are deliberate rather than incidental:

```powershell
& $python scripts/validate_all.py --offline --json-output <path-outside-this-repo>.json
```

- **The receipt must land outside the repository.** Writing it inside would
  dirty the very source identity being validated, and the run fails closed.
- **The worktree must be clean.** The receipt binds a real `HEAD`; uncommitted
  changes mean the receipt describes something that does not exist as a commit.

The run also fails when a declared command is missing, when source identity
changes mid-execution, or when any real exit code is nonzero. A green result
records offline and live status separately and never runs live checks by
default.

## What CI adds on top

Every push runs the same offline surface across twelve cells: Ubuntu, Windows,
and macOS, on Python 3.11 and 3.13, against two pinned Codex CLI versions. It
also proves the release archive builds byte-identically twice. A change that
passes locally on one platform can still fail there, most often on path
handling or line endings.

Tag pushes run the full release path, and that is the only place the real
install, upgrade, and rollback are exercised against a published tag.

## Style

- Python is formatted and linted with `ruff`; `ruff format --check .` and
  `ruff check .` are both part of the validation surface.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat:`, `fix:`, `refactor:`, `chore:`, `docs:`, `test:`, `build:`. Keep the
  subject imperative, lowercase after the prefix, no trailing period.
- Match the style of the file you are editing, and keep diffs scoped to the
  change. Comments explain non-obvious reasoning, not restated code.

## Changing a skill

Skills live in `skills/` for Claude Code and `skills-core/` for Codex, packaged
from one source tree. `scripts/validate_skills.py --strict-quality` enforces the
contract for both. If a change affects routing, add cases to
`benchmarks/skill_routing_cases.json` — a routing change with no case is a
change nobody can verify.

## Reporting something instead

Bugs and proposals are welcome as issues. For anything security-sensitive, see
[SECURITY.md](SECURITY.md) rather than opening a public issue.
