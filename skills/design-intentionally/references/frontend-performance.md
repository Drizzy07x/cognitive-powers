# Frontend performance audit

Run the bounded static audit only for projects that declare React or Next.js:

```powershell
& $python <skill-root>/scripts/frontend_performance.py --root <project>
```

Treat every result as a review candidate. The audit is version-bound: it records declared framework versions but does not infer version-specific recommendations. It intentionally refuses to claim measured runtime performance or a proven optimization. Before changing code, invoke `use-current-docs` for the detected version and confirm the recommendation against the real bundle, render, or network behavior.

The current rules identify:

- a Next.js root layout whose client boundary may be broader than necessary;
- raw `img` and `script` elements that need framework-aware review;
- selected large packages imported statically from client components.

Use `--fail-on-warning` only as a narrow quality gate. Advisories never fail the command because valid exceptions are common.
