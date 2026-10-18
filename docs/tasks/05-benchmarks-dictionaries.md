Read AGENTS.md and DESIGN.md step 8.
Implement immutable content-addressed policies, repository-level dataset partitions,
training-only zstd dictionaries with provenance and explicit small-sample fallback,
portable embedded dictionary archives, and local benchmark CLI reports.
Preserve V1 decoding and reject missing/corrupt assets and unsupported versions.
Compare identical snapshots with plain RPW and normalized whole-tree tar baselines;
record full bytes, exact reconstruction, repeated timings, variance, process peak RSS,
environment, explicit resource limits, and failure/ineligibility reasons.
Test archive overhead, partition overlap, failed training/decoding, and scoring gates.
Run locked pytest, Ruff, and mypy. Do not add agents, remote execution, or dependencies.
