# RepoWeave
Group Repository for HackHarvard 2026

Implementation through DESIGN.md step 9: lossless archives, static Python repository
graphs, trained dictionaries, measured benchmarks, and bounded Factory proposals.

```bash
uv sync --locked
uv run --locked repoweave analyze examples/demo_repository --output .repoweave/analysis.json
uv run --locked repoweave benchmark examples/demo_repository --policy policies/baseline.json --output .repoweave/benchmark.json
```

Use fresh output names on repeat runs. No service credentials are required.

- [Archive usage and supported scope](docs/archive-usage.md)
- [Repository graph](docs/repository-graph.md)
- [Benchmarks, training partitions, and dictionary policies](docs/benchmarks.md)
- [Factory agents and the opt-in live test](docs/factory-agents.md)
- [Daytona engine snapshot setup](docs/daytona-snapshot.md)
- [Implementation verification](docs/implementation-status.md)

Optimization/cloud workflows and the dashboard remain later steps. Compression improvements
are hypotheses; compare full archives and resource measurements before making claims.
