# RepoWeave
Group Repository for HackHarvard 2026

Local implementation through DESIGN.md steps 7–8: lossless archives, static Python
repository graphs, trained dictionaries, versioned policies, and measured benchmarks.

```bash
uv sync --locked
uv run --locked repoweave analyze examples/demo_repository --output .repoweave/analysis.json
uv run --locked repoweave benchmark examples/demo_repository --policy policies/baseline.json --output .repoweave/benchmark.json
```

Use fresh output names on repeat runs. No service credentials are required.

- [Archive usage and supported scope](docs/archive-usage.md)
- [Repository graph](docs/repository-graph.md)
- [Benchmarks, training partitions, and dictionary policies](docs/benchmarks.md)
- [Implementation verification](docs/implementation-status.md)

Agent/cloud workflows and the dashboard remain later steps. Compression improvements
are hypotheses; compare full archives and resource measurements before making claims.
