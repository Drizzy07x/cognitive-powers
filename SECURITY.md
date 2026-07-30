# Security Policy

## Supported versions

Only the latest released version receives fixes. Older tags remain published for
rollback and reproducibility, but are not patched.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Use GitHub's private vulnerability reporting instead:
[report a vulnerability](https://github.com/Drizzy07x/cognitive-powers/security/advisories/new).
That channel is private until an advisory is published, so a fix can ship before
the details are public.

Useful things to include, when you have them: the affected version or tag, the
platform and Python version, and the smallest reproduction you can manage.

## Scope worth knowing about

This plugin executes local commands and reads local state by design, so the
interesting boundaries are narrower than "it runs code":

- The installer resolves a release tag to an immutable commit and verifies it.
  A path that lands a different commit than the tag names is in scope.
- Durable session state is written outside the target repository, and an
  override resolving inside it is rejected before state is created. A bypass of
  that rejection is in scope.
- Optional providers (Playwright, Skyvern, QCU) are never installed or started
  by validation. Anything that starts live browser or desktop input without
  explicit authorization is in scope.
