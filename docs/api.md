# Local API and persistent worker (DESIGN Step 13)

Start both processes from the same repository root and configured data directory:

```bash
uv sync --locked
uv run --locked uvicorn repoweave.api.app:app --host 127.0.0.1 --port 8000
# In another terminal:
uv run --locked python -m repoweave.runs.worker
```

The API persists requests before returning HTTP 202. One worker owns execution under
a filesystem lock and uses the same CLI engine and optimization service as local
commands. API shutdown or browser disconnect does not cancel work. Completed runs,
events, checkpoints, and published artifacts live in `REPOWEAVE_DATA_DIR`.
`python -m repoweave.runs.worker --once` processes one available job and exits.

This is a local, single-user service. It rejects remote clients, unexpected Host
headers, and cross-origin browser requests. Bind to loopback, without a public proxy.
Authentication, multi-user ownership, and public hosting are outside Step 13.

## Register allowed fixtures

The operator configures fixture aliases in `.env` or the process environment:

```dotenv
REPOWEAVE_FIXTURES='{"demo":"examples/demo_repository"}'
```

The default is the existing demo fixture. The API accepts only these names, never
browser-supplied paths. Registration captures an independent immutable snapshot;
later edits to the original fixture do not change already submitted work.

```bash
curl -X POST http://127.0.0.1:8000/repositories \
  -H 'Content-Type: application/json' -d '{"fixture":"demo"}'
```

Use the returned `id` as `repository_id` below. `GET /repositories/{id}/graph` returns
the actual static repository graph. `GET /workflow` returns the actual LangGraph
nodes and edges used by optimization, rather than a separate hard-coded diagram.

## Submit and control work

`POST /runs` requires an `Idempotency-Key` header (1–128 letters, digits, `_ . : -`).
Repeating the same normalized request with the same key returns the same job;
reusing a key for different inputs returns HTTP 409.

```json
{
  "repository_id": "REPLACE_WITH_REGISTERED_ID",
  "operation": "benchmark",
  "policy": {"codec": "zstd", "zstd_level": 3},
  "execution": "local",
  "repeats": 2,
  "warmups": 1
}
```

Operations are `pack`, `benchmark`, and `optimize`. Benchmarks require at least two
measured repetitions. Requests may include `limits` (`encode_seconds`,
`decode_seconds`, `peak_memory_bytes`). Factory is used only by optimization.
Cloud optimization additionally requires `execution: "daytona"`,
`approved_snapshot` equal to the registered repository ID, configured Daytona
credentials/snapshot, and `REPOWEAVE_ALLOW_REMOTE_DATA=true` in the backend.

Dictionary policies reference bundles already trained and registered through the
CLI in this data directory. Supply `dictionary_ids`, the full `splits` object, and
`partition_repository_id` identifying this repository in the validation partition.
Training identities and content snapshots must not overlap validation. Graph-grouped
benchmarks also need the matching extension-grouped bundle in `dictionary_ids`.
No API input can specify dictionary filesystem paths.

| Endpoint | Result |
| --- | --- |
| `GET /health` | Database readiness, without settings or credentials |
| `GET /runs` | Persisted API submissions |
| `GET /runs/{id}` | Status, available comparison metrics, and artifact IDs |
| `GET /runs/{id}/events` | SSE progress with monotonic sequence IDs |
| `POST /runs/{id}/approve` | Approve a real persisted workflow interrupt |
| `POST /runs/{id}/resume` | Queue approved or interrupted work |
| `POST /runs/{id}/cancel` | Cancel queued work or signal active execution |
| `GET /artifacts/{id}` | Download an explicitly published, hash-verified artifact |

Approval does not itself resume execution. An improving run pauses at
`awaiting-approval`; approve it, then resume it. Promotion retains the existing
transactional compare-and-swap against the parent policy. Non-improving runs finish
as `completed-without-improvement`; pack and benchmark jobs finish as `completed`.
Terminal runs cannot be approved or restarted. A new experiment uses a new key.

SSE clients reconnect with `Last-Event-ID` or `?after=SEQUENCE`. Use `?follow=false`
for a finite replay. Streams close at terminal, interrupted, or approval states;
reconnect after a resume. Disconnecting the stream leaves execution untouched.

Only artifacts explicitly published by this API's worker are downloadable. Knowing
a raw internal report hash or local path does not grant download access. Downloads
reject symlinks, changed hashes, and oversized files. Validation and worker errors
omit request values and exception payloads.

## Recovery

The queue and status transitions are transactional. A worker restart reclaims an
unfinished running submission under the same exclusive lock. Optimization reuses
its existing checkpoints and revalidates completed engine jobs. It never blindly
reruns an uncertain remote experiment; unrecoverable remote execution/export requires
the existing `repoweave runs cleanup RUN_ID` and a new run. An interrupted pack or
benchmark can retry in a fresh temporary workspace; its operations only read the
registered source, and completed outputs are published by content hash.

SIGTERM/Ctrl-C interrupt the worker and stop its active local child process group.
Queued jobs remain queued; the interrupted job can be explicitly resumed. A requested
cancellation is terminal. Abrupt process death can leave local measurement children
finishing their bounded work; this is not an OS-enforced resource-isolation system.

The HTTP implementation uses FastAPI's [streaming response support](https://fastapi.tiangolo.com/advanced/custom-response/)
and Starlette's [trusted-host middleware](https://www.starlette.io/middleware/).
