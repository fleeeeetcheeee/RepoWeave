# RepoWeave
Group Repository for HackHarvard 2026

Implementation through DESIGN.md steps 10–14: lossless archives, static Python repository
graphs, trained dictionaries, measured optimization with Factory, optional LangSmith
traces, bounded Daytona experiments with a separate decoder sandbox, and a local API
with a persistent job queue, separate worker and local React dashboard.

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
- [Optimization, approvals, cloud execution, and tracing](docs/optimization.md)
- [Local API and persistent jobs](docs/api.md)
- [Local dashboard and reproducible demo](docs/dashboard.md)
- [Daytona engine snapshot setup](docs/daytona-snapshot.md)
- [Implementation verification](docs/implementation-status.md)

Public deployment is optional; neural codecs remain a later step. Compression improvements
are hypotheses; compare full archives and resource measurements before making claims.
