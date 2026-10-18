# Factory reasoning over measurements

Step 9 provides `analyze`, `propose`, and a deterministic `report_comparison`.
They do not run experiments, write policies, or promote candidates. The optimization
workflow and its CLI commands belong to step 10.

Install with `uv sync --locked`. The backend also needs `droid` on PATH. Authenticate
with the CLI or put `FACTORY_API_KEY` in the ignored backend `.env`. The application
loader passes the key to the SDK without printing it or adding it to arguments.
Leave `REPOWEAVE_FACTORY_MODEL` blank to use the Factory default, or use an
account-supported model ID. See the [Factory SDK documentation](https://docs.factory.com/sdk/python).

The authorized live smoke test sends only invented numeric measurements:

```bash
uv run --locked pytest -m live tests/live/test_factory.py --tb=short
```

It makes one analyst call and one proposer call and may incur Factory charges.
Ordinary `uv run --locked pytest` excludes it. Missing authentication or a service
failure is a test failure, not a skip or a simulated success.

For application use, first project a locally approved benchmark report through
`summarize_benchmark(report)`. This retains numeric samples, resource limits, methods,
and content IDs. Paths, file bodies, names, free-form errors, and environment details
are omitted. Result IDs are hashes of the selected measurement and its position in
the snapshot's report. Final holdout reports are rejected. Then call:

```python
from repoweave.agents.factory import analyze, propose
from repoweave.agents.schemas import summarize_benchmark

# Inside an async application entrypoint; report, parent, and settings are local inputs.
evidence = summarize_benchmark(report)
diagnosis = await analyze(evidence, settings)
proposals = await propose(evidence, diagnosis, parent, settings)
```

Explicitly approve any real measurement data for external processing before using
these functions. The caller owns that authorization and the truth of the supplied
measurements; schema validation cannot prove measurements are genuine.

Proposals are limited to `min(3, settings.max_candidates)` and Zstd levels -7 through
22. Dictionary choices default to none. To allow dictionaries, pass `dictionary_ids`,
`splits`, and a validation `repository_id` to `propose`. Bundles are loaded from
`settings.data_dir / "dictionaries"` and checked before and after the model call for
identity, assets, matching partitions, successful training, and absence of training/
evaluation overlap. Each candidate's grouping must match its registered bundle.
Graph grouping still requires the existing extension-control benchmark when tested.

Every output is strictly validated. Evidence IDs must occur in the supplied report;
candidate parent IDs must match the measured parent. Model explanations remain
untrusted prose. They are never executed, used as paths, or treated as verified gains.
The deterministic reporter checks reconstruction, limits, and smaller complete archive
size before accepting an `accepted` decision; it preserves an explicit rejection.

Each call owns a fresh temporary workspace, session, and stream. Prompts ship inside
the wheel. Permission requests are rejected and built-in skills are disabled.
Droid 0.244.0 treats an empty restrictive allowlist as unrestricted, so the integration
allows only `Read` and explicitly disables `Read`. It checks the effective session
settings, zero allowed tools, and zero MCP servers before sending any measurement.
Unexpected tools/MCP configuration fail closed. A tool or hook event also aborts a
turn. This is a CLI policy boundary, not OS process isolation; use a trusted backend
CLI profile without custom lifecycle hooks.

`REPOWEAVE_EXPERIMENT_TIMEOUT_SECONDS` bounds setup plus streaming. SDK failures,
timeouts, and malformed/partial outputs become secret-safe `AgentRunError` messages;
task cancellation propagates. Context managers close streams/processes and remove
the owned workspace. Factory may retain its own session history under its configured
profile; only approved summaries should be supplied. The engine and decoder never
import the agent modules or need Factory credentials.
