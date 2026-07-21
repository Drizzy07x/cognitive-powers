# Python 3.14 Zstandard decision

Decide whether a dependency-free application targeting Python 3.14 should
standardize on `compression.zstd` for its Zstandard support.

Requirements:

- read and write `.zst` files;
- one-shot in-memory compression and decompression;
- incremental compression and decompression;
- no third-party runtime dependency;
- deployments include official CPython builds and distributor-provided builds.

Return a recommendation supported by the supplied current, version-matched
primary sources. Separate verified facts, your inference, and unresolved
uncertainty. Include claim-level links to the exact official pages.
