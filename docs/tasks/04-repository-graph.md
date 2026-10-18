Read AGENTS.md and DESIGN.md step 7.
Implement static Python import analysis of copied snapshots, stable directed JSON,
unresolved/syntax diagnostics, graph and extension grouping, and deterministic
acyclic candidate context ordering. Expose it through analyze without executing input.
Keep repository edges distinct from candidate context and actual decode dependencies.
Test imports, cycles, parse failures, grouping, stability, and source non-execution.
Run locked pytest, Ruff, and mypy. No agents, cloud services, or dependency changes.
