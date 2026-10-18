# Optimization, cloud execution, and tracing

`repoweave optimize` runs one bounded search round with at most three proposals.
It benchmarks a captured validation snapshot, asks Factory for evidence-linked
proposals, measures them with the same environment and conventional tar baselines,
and independently validates exported archives. Warmups are excluded. A smaller
eligible RPW archive pauses for explicit approval; no improvement is a valid outcome.
This does not implement the dashboard, API, or neural codecs.

```bash
uv run --locked repoweave optimize examples/demo_repository --policy policies/baseline.json --execution local
uv run --locked repoweave runs list
uv run --locked repoweave runs show RUN_ID
uv run --locked repoweave runs approve RUN_ID
uv run --locked repoweave runs resume RUN_ID
uv run --locked repoweave runs cancel RUN_ID
```

`--run-id` accepts a 64-character lowercase hexadecimal idempotency key. Repeating
an identical submission reuses the run; changed submissions are rejected. SQLite
stores run/event/job receipts and LangGraph checkpoints. A process lock allows one
worker per run. Resume revalidates downloaded completed jobs and never blindly
resubmits an uncertain experiment. Failed or cancelled runs are terminal; start a
new run after investigating. A killed worker can resume its checkpoint. Approval
requires an actual persisted interrupt, and promotion uses a transaction against
the expected active parent. Immutable selected policies live in `policies/` under
the configured data directory. Existing archives remain self-contained.

The compiled Studio graph in `langgraph.json` uses the same nodes and no application
checkpointer or import-time clients. Invoking it requires the backend's `Context`
(settings, Store, Runner, agents and Tracer); the CLI supplies this context and owns
SQLite persistence. The graph does not load `.env` on import.

## Daytona

Use the existing engine snapshot; no rebuild is needed for backend-only changes.
Enable `REPOWEAVE_ALLOW_REMOTE_DATA=true` locally and explicitly authorize the exact
snapshot identity obtained from `analyze`:

```bash
uv run --locked repoweave optimize examples/demo_repository --policy policies/baseline.json --execution daytona --approve-snapshot SNAPSHOT_ID
uv run --locked repoweave runs cleanup RUN_ID
```

Creation intent, unique name, ID, and cleanup state are persisted. The experiment
sandbox and decoder sandbox run sequentially, each limited to 2 CPUs, 4 GiB RAM,
10 GiB disk, and ten minutes. Outbound networking is blocked. Commands clear the
environment and use the installed engine entrypoint under a remote timeout.
Credentials remain in the backend. Only the captured input archive, validated
request, and selected registered dictionary bundle enter experiment execution.
`--dictionary-id`, `--splits`, and `--repository-id` enable registered training
assets with validation-partition checks.

Downloads stream through byte limits and are validated against request identities,
SHA-256, declared sizes, and local exact reconstruction before becoming completed
jobs. The separate decoder receives only the selected portable archive. Its
restored archive returns to the backend for actual byte/inventory/executable-bit
comparison. Original input and training data never enter that sandbox.

Both sandboxes are deleted after export, including failure/cancellation cleanup.
If cleanup fails, the ledger retains `cleanup-pending`; retry `runs cleanup`.
Cleanup does not need staging authorization. If an account denies explicit deletion,
the runner waits up to 30 seconds for configured immediate auto-deletion after stop
and requires a not-found response. A stop or access-denied response alone never
counts as deletion. A creation timeout is reconciled by
its existing name instead of creating another sandbox. A not-yet-visible uncertain
creation remains pending until reconciliation or its ten-minute lifetime expires.
The provider TTL and auto-stop are backstops; interrupted jobs may be unrecoverable
when their sandbox expires. Durable exports remain local.

## LangSmith

Tracing is optional and defaults to disabled. `LANGSMITH_TRACING=true` uses the
configured API key, project, and optional regional endpoint at runtime. Factory
and graph automatic tracing are disabled by the application runner; explicit spans
export only content IDs, fixed node/outcome names, timestamps, and measured duration.
The selected Factory model is represented by a content hash. Source, paths, prompts,
model prose, settings, exception messages, and environment metadata are excluded.
The SDK client is created lazily, with automatic runtime metadata disabled and
additional input/output filtering. Trace failure is recorded in local events and
never changes archive verification or promotion eligibility.

The integration follows LangSmith's [programmatic configuration](https://docs.langchain.com/langsmith/trace-without-env-vars)
and LangGraph's [persisted interrupt/resume contract](https://docs.langchain.com/oss/python/langgraph/interrupts).
The installed SDK signatures and wire payloads are tested locally.

## Opt-in live check

```bash
REPOWEAVE_LIVE_CLOUD=1 uv run --locked pytest -m live tests/live/test_cloud_optimization.py -q -s --tb=short
```

This authorizes only the test's generated owned fixture, sanitized Factory evidence,
LangSmith reference spans, and two disposable sandboxes. The test checks a complete
optimization outcome, artifact transfers, clean independent decoder, byte equality,
trace readback/redaction, and cleanup. It does not approve policy promotion. Receipts
and all downloadable artifacts remain under `.repoweave/live-cloud/`. The default
pytest suite excludes live tests.

The separate `tests/live/test_langsmith.py` test uses the same opt-in flag and sends
only synthetic trace references, without model calls or sandboxes. LangSmith readback
is eventually consistent; both live checks retry missing/incomplete spans for up to
45 seconds. To recheck an already completed generated cloud run without provisioning
or model calls, also set `REPOWEAVE_LIVE_CLOUD_RECHECK` to its 32-character receipt
folder name. Rechecks write a new receipt and preserve the original outcome.

## Extended live coverage

`tests/live/test_extended_cloud.py` covers two larger pinned public releases and an
owned dictionary-transfer/promotion workflow. Download only the exact public archives
listed in that test to `/tmp/repoweave-django-5.1.4.tar.gz` and
`/tmp/repoweave-sympy-1.13.3.tar.gz`; their SHA-256 values are checked before extraction.
`REPOWEAVE_PUBLIC_ARCHIVES` can select another archive directory. Never substitute
private data. The tests do not execute downloaded source.

```bash
env REPOWEAVE_LIVE_EXTENDED=1 uv run --locked pytest -m live tests/live/test_extended_cloud.py -q -s
```

This explicitly opts into three cloud runs, each with an experiment sandbox and a
separate decoder. The dictionary run uses a fixed proposer, real measurements, and an
explicit approval in an isolated test store. It cannot activate a policy in your normal
store. It checks actual embedded dictionary bytes, exact reconstruction, promotion once,
repeated resume without new jobs, and confirmed deletion. It does not claim dictionary
compression is better than plain zstd. See the retained [verification results](implementation-status.md).
