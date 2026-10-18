# Local dashboard (DESIGN step 14)

The React/Vite/TypeScript dashboard uses the persistent API and a separate worker.
It runs locally without Fly, Auth0, a domain, or public deployment. Packing and
benchmarking need no service credentials. Optimization uses the backend's Factory
Droid CLI; optional Daytona execution also needs the configured engine snapshot.
Public authenticated hosting remains a separate, optional integration described in
[hosting setup](hosting-setup.md). This dashboard currently targets the local API;
it does not implement Auth0 login.

## Start a fresh demo

Use Node 24 LTS (`web/.nvmrc`) and the checked-in dependency locks:

```bash
uv sync --locked
cd web
npm ci
cd ..
bash scripts/run_demo.sh
```

Open `http://127.0.0.1:5173`. The script creates an owned synthetic fixture and a
fresh private directory under `.repoweave/demos/`, then starts API, worker, and Vite.
It prints the directory for later recovery. Ctrl-C stops those processes and retains
the database, snapshots, events, and outputs. It never executes fixture source files.
Use `bash scripts/run_demo.sh --live` to enable the Daytona option. Each cloud
submission additionally requires approval of the selected immutable snapshot in
the browser. This transfers only the generated demo snapshot. Service credentials
stay on the backend; Vite's only setting is the public API base URL.

1. **Repository:** capture `demo`, inspect the included/excluded inventory and static
   import graph. The fixture includes a cycle, binary bytes, Unicode, an executable
   file and an empty directory.
2. **Archive:** choose a policy and create an archive. The worker independently
   decodes in a separate process with an empty service environment, compares exact
   bytes and inventory, and records a verification receipt. Download the RPW file.
3. **Benchmarks:** run a comparison. Inspect full archive bytes, repeated timing,
   process-lifetime peak RSS, overhead, failed/ineligible methods and raw samples.
4. **Optimization:** run a bounded Factory proposal and experiment, inspect the
   actual workflow topology, persisted events, evidence and decision. Only an
   eligible verified candidate enables **Approve & promote**. New archives can use
   the promoted policy; existing archives keep their embedded decoder assets.
5. Repeat optimization with **Daytona sandbox** and approve the snapshot transfer.
   Inspect the independent decoder receipt and both sandbox deletion states.
6. Refresh: the URL restores the selected snapshot/run; the API reloads durable
   state and SSE replays persisted events. Interrupted runs can be resumed, active
   work cancelled, and report artifacts downloaded.

For a reproducible promotion demonstration, uncheck **Use active policy** and choose
**Stored (uncompressed control)** before the initial pack and local optimization.
An improvement over that control demonstrates promotion, not superiority over zstd.
Whole-tree tar-zstd results remain visible. A cloud run may honestly retain the
active policy when no smaller eligible candidate exists.

The dashboard submits ordinary stored/zstd/lzma policies. Dictionary training and
partition-aware submissions remain CLI/API operations. Inventory and graphs bound
their displayed rows/nodes, with explicit counts; source content is not displayed.

## Resume the same workspace

Use the printed demo directory in two terminals, then start Vite in a third:

```bash
.venv/bin/python scripts/dashboard_backend.py api --root .repoweave/demos/DEMO_DIRECTORY
.venv/bin/python scripts/dashboard_backend.py worker --root .repoweave/demos/DEMO_DIRECTORY
cd web && npm run dev
```

Add `--live` to both Python commands only when cloud execution is intended. Never
start a second worker for the same directory. Refreshing or closing a browser does
not cancel a job. The launcher uses fixed loopback ports 8000/5173 and fails if they
are occupied.

## Reproduce acceptance checks

```bash
cd web
npm run generate:api
npm run build
npm run test:e2e
# Separately labeled LIVE test: real Factory calls and two Daytona sandboxes.
REPOWEAVE_LIVE_DEMO=1 npm run test:e2e
```

The browser suite uses installed Google Chrome. Default mode replaces only Factory
and Daytona boundaries; compression, API, worker, persistence, artifact downloads,
separate-process decoding and comparison are real. It needs no cloud credentials or
installed Droid CLI. Live mode reads backend configuration and incurs service usage.
Each invocation uses a fresh directory, stops its local servers, and writes
`browser-receipt.json` plus exported artifacts and desktop/mobile screenshots.
The cloud executor exports and verifies artifacts before deleting either sandbox.

The test covers eligible promotion, new-policy packing, decoding the old archive
again after promotion, a cloud experiment with independent decoding, refresh
recovery, SSE reconnect after a real event sequence, and a 390-pixel layout.
An additional local-only test simulates a lost response after the backend accepted
a submission, checks the displayed error, and verifies retry creates no duplicate.
See [implementation status](implementation-status.md) for the checks actually run
and the separately labeled live result. No public hosting acceptance is implied.

API types and `web/openapi.json` are generated by `npm run generate:api` without
reading `.env` or starting any service. Run it after changing API schemas.
