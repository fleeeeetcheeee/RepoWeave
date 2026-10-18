# Implementation verification

## Step 14 — local dashboard and live demonstration (October 18, 2026)

Implemented the React/Vite/TypeScript Repository, Archive, Benchmarks and
Optimization views against real persisted API responses. Types are generated from
OpenAPI. The UI shows the actual repository/workflow graphs, measurements, evidence,
approval state, downloads, verification receipts and sandbox lifecycle. It restores
the selected run after refresh and reconnects SSE after the last event sequence.
Only eligible verified candidates enable promotion. Errors, cancellations and
no-improvement outcomes remain explicit. A lost submission response can be retried
with the same idempotency key without duplicating the job.

The API exposes saved snapshot/policy reads and allowlisted reports. Before publishing
an archive the worker independently decodes it in a separate process without service
credentials, then verifies exact supported bytes/inventory. CORS permits only the
exact local dashboard origin in addition to existing local API access. The archive
format, codecs and deterministic compression engine were not changed.

**Live smoke demonstration: passed**, using the owned generated fixture (11 files,
97,409 included bytes), real Factory analyst/proposer calls and real Daytona execution.
The UI packed, downloaded and independently decoded the original archive; benchmarked
it; promoted an eligible local candidate; packed with the promoted policy; decoded
both the new archive and the old archive again; ran the cloud experiment; reloaded
the same persisted run; and exported its artifacts. The original stored control was
99,754 archive bytes and the selected zstd archive was 3,472 bytes. This demonstrates
promotion from an uncompressed control, **not superiority over plain zstd**. The
subsequent cloud search found no further improvement and correctly retained 3,472
bytes. Independent cloud reconstruction passed. Experiment and decoder sandboxes
both have confirmed `deleted` lifecycle receipts; the executor exported verified
results before cleanup.

Durable local evidence (ignored, intentionally not committed):

- Mocked-boundary browser receipt: `.repoweave/demos/e2e-1792304940025/browser-receipt.json`.
- Live browser receipt: `.repoweave/demos/live-1792304715499/browser-receipt.json`.
- Live local promotion: `5dcf2c87e6e6d19818acfd94f87e6306bac0d8a8b6722fc3db99208c1f6474b0`.
- Live cloud run: `b657461f75975c1dd42809c552ace7be3580b82548d4703663eb94b348cb05c3`.
- Both demo directories contain persistent stores and `exports/` with downloaded
  artifacts, independent reconstructions and desktop/mobile screenshots.

Checks actually run:

```text
.venv/bin/pytest -q tests/integration/test_dashboard_api.py tests/integration/test_api.py tests/integration/test_hosting.py tests/integration/test_hosted_server.py tests/integration/test_optimization.py --tb=short
58 passed in 26.81s

cd web && npm run test:e2e
2 passed in 21.0s (mock Factory/Daytona boundaries; real engine)

cd web && REPOWEAVE_LIVE_DEMO=1 npm run test:e2e
1 passed in 2.7 minutes (separately labeled live demonstration)

cd web && npm run generate:api
cd web && npm run build
Passed: generated types, strict TypeScript and production bundle

.venv/bin/ruff check src tests scripts
All checks passed

.venv/bin/mypy src/repoweave
Success: no issues found in 67 source files

bash -n scripts/run_demo.sh
bash scripts/run_demo.sh
Passed: fresh directory, API/worker/Vite readiness, Ctrl-C graceful shutdown,
no remaining listeners on ports 8000/5173

git diff --check
Passed
```

The 58-test suite includes API restart, checkpoint resume, cancellation, real
project isolation, promotion conflicts and remote failure cleanup. Browser coverage
includes an intentionally cut SSE stream with a nonzero resume cursor, page refresh,
lost-response retry and a 390-pixel layout without page overflow. Desktop/mobile
screenshots were inspected. The final loading/dictionary-policy control guards and
additional local failure-injection test were verified locally after the live run;
the live cloud demo was not repeated solely for those UI changes.

Public deployment remains **optional and unprovisioned**: no Fly resources, remote
build or public dashboard were created. Auth0 login integration and real public
multi-user browser acceptance remain untested. This task did not reread or upload
private financial datasets. Dictionary training/partition-aware submission stays
in the CLI/API; the dashboard currently submits ordinary stored/zstd/lzma policies.
Cross-browser/OS coverage and the full Python regression suite were not rerun for
this step. See [dashboard instructions](dashboard.md) to launch or resume a demo.

## Fly/Auth0 setup follow-up — October 18, 2026

The user approved Auth0 registration and explicitly chose **prepare only, no
charges** for Fly. In the existing development tenant, created RepoWeave Dashboard
as a Single Page Application and RepoWeave API with audience `https://repoweave-api`,
RS256 and permission `repoweave:use`. Saved maximum and implicit/hybrid access-token
lifetimes of 900 seconds. API user-delegated access is per-app; the Dashboard has
exactly this one permission (verified in the dashboard). Client/M2M access is denied
by API policy. Auth0 automatically created an API test application; it has no usable
client access under that policy. Existing Default App access was not granted.

Public callbacks/logout/origins remain empty because no Fly hostname is reserved.
No user passwords, client secrets or signing keys were read or copied. Tenant/client
public configuration and a hosting example are saved under ignored `.repoweave/hosting/`.
Actual application-user subjects are still required before deployment.

Installed the official Fly CLI under ignored `.repoweave/tools/fly/`. Prepared the
container, explicit source/demo build allowlist, single-Machine TOML, persistent-volume
entrypoint and API/worker supervisor. No Fly app, volume, IP, remote build or Machine
was created; no source/dataset upload occurred. The CLI's strict configuration
validation could not run without Fly CLI login. Local TOML parsing and host/VM
assertions passed. Docker is not installed here, so the image has not been built.

Checks on the final code:

```sh
.venv/bin/pytest -q tests/integration/test_hosted_server.py tests/integration/test_hosting.py --tb=short
.venv/bin/ruff check src tests scripts
.venv/bin/mypy src/repoweave
git diff --check
```

Results: **11 passed in 3.59s**, Ruff passed, mypy passed for **67 source files**,
and whitespace checks passed. New tests cover sibling shutdown after child failure,
forced shutdown of an unresponsive child, partial startup failure, safe configuration
errors and graceful SIGTERM. Existing real JWT/project isolation and worker tests ran.
No dependency or lockfile versions changed. Full regression suite was not rerun in
this setup-only follow-up. Real user login/token issuance, public TLS, public
cross-user isolation, container execution and volume backup/recovery remain untested.
See [setup instructions](hosting-setup.md) for the remaining steps.

Verified locally on macOS with Python 3.12.15 and the existing `uv.lock`.

## Step 6 baseline

Before changes, the existing suite passed **316 tests** across `tests/unit` and
`tests/integration`; Ruff and mypy also passed (23 source files). Inspection and tests
covered the specified archive scope, stored/zstd/lzma and mixed round trips, bounded
decoding, canonical manifests, unsafe paths, corrupt inputs, metadata, output races,
interruption cleanup, and independent credential-free decoding. No step 6 blocker was
found within that local verification scope.

## Steps 7 and 8

Implemented static Python import analysis, stable directed graph JSON, diagnostics,
graph/extension groups, and separately labeled acyclic candidate context ordering.
Added training-only dictionary bundles with provenance, disjoint repository partitions,
immutable policy IDs, portable RPW V2 dictionaries with V1 compatibility, and the local
benchmark command with fresh-process measurements and whole-tree tar baselines.

Graph dictionary comparisons require an extension grouping control using the same
training repository snapshots and codec/level. Test partitions require explicit final
reporting; retired holdouts and training/evaluation overlap are rejected. Failed,
unverified, or resource-ineligible measurements cannot receive winning scores.

The final regression commands were:

```bash
uv run --locked pytest tests/unit tests/integration
uv run --locked ruff check src tests
uv run --locked mypy src/repoweave
git diff --check
```

Results: **367 tests passed**, Ruff passed, mypy passed for **36 source files**, and
the whitespace check passed. The dependency manifest, lockfile, and committed baseline
policy were not changed. Targeted iterations also ran the new graph, dictionary,
objective, benchmark, and training-workflow tests via the installed `.venv/bin` tools.

New coverage includes successful and failed dictionary training; missing/corrupt assets;
full embedded asset and manifest overhead; independent dictionary decoding without the
training registry; policy independence; partition overlap and alias checks; static source
non-execution; graph cycles/syntax/encoding failures; normalized tar byte reconstruction;
actual child-process benchmarks; child timeouts; warmup exclusion; late decode failures;
memory/time eligibility; and graph-versus-extension comparisons on one evaluation snapshot.

A local CLI smoke run also analyzes and benchmarks the owned `examples/demo_repository`
with the committed baseline, default one warmup, and three measured repetitions.
Its metadata reports are kept in an ignored `.repoweave/step78-verification-*` directory.

## Step 8.4 local scale gate

Added `tests/integration/test_large_repositories.py` to the ordinary pytest suite;
no new markers, dependencies, services, or production behavior were needed.
Fixtures are generated locally in `tmp_path`, and CLI subprocesses run with `env={}`.
The synthetic source is only analyzed as data. Both workloads run analysis followed
by candidate RPW, baseline RPW zstd, tar-zstd, and tar-lzma, each with one discarded
warmup and two measured repetitions in fresh child processes.

The targeted command was:

```bash
uv run --locked pytest tests/integration/test_large_repositories.py
```

Result: **2 passed in 23.58 seconds**, on macOS 27.0.1 arm64, Python 3.12.15.
An initial test-only report-key typo was corrected before this passing run.

| Workload | Files | Included bytes | Maximum measured process RSS across methods |
| --- | ---: | ---: | ---: |
| Many small files, including a 2,048-module import cycle | 2,114 | 2,427,593 | 100,892,672 bytes |
| 64 MiB and 56 MiB repeated files, 8 MiB seeded binary, metadata/empty files | 5 | 134,217,747 | 210,092,032 bytes |

All four methods reconstructed exact bytes, inventory, and executable bits on both
workloads, produced repeatable archive sizes, and stayed within the explicit
300-second encode/decode and 1 GiB RSS limits. The deep graph had all 2,048 edges;
candidate context removed one cycle edge and ordered all remaining dependencies.
Analysis and benchmark snapshot identities matched, RPW full-size accounting passed,
and benchmark temporary workspaces were removed. Raw `analysis.json` and
`benchmark.json` reports remain in pytest's per-test temporary directories under its
normal retention policy. These synthetic measurements are regression evidence only.

The same full regression commands listed above were then rerun: **369 tests passed
in 32.24 seconds**, Ruff passed, mypy passed for **36 source files**, and
`git diff --check` passed. `pyproject.toml`, `uv.lock`, and the committed baseline
policy are unchanged.

## Step 9 Factory agents

Implemented `agents/schemas.py`, `factory.py`, packaged role prompts, and a
deterministic comparison reporter. The integration projects benchmark results onto
bounded numeric evidence and content IDs, rejects final holdout reports, validates
diagnoses and proposals, and checks registered dictionary assets and training
partitions before and after reasoning. It neither executes experiments nor modifies
active policies. See [Factory usage](factory-agents.md).

Verified October 17, 2026 on macOS arm64 / Python 3.12.15 with `droid-sdk==0.5.0`,
`pytest-asyncio==1.4.0`, and Droid CLI **0.244.0**. These two dependencies were added
to the lockfile without changing existing package versions.

The initial live preflight failed closed before prompting: the installed CLI treated
an empty restrictive allowlist as unrestricted. Prompt-free probes established that
allowing only `Read` and explicitly disabling `Read` produces zero allowed tools.
The implementation now uses that configuration, verifies the effective settings and
tool list, and rejects inherited MCP servers. DESIGN.md documents the correction.

Final verification:

```bash
uv run --locked pytest
uv run --locked pytest -m live tests/live/test_factory.py --tb=short
.venv/bin/ruff check src tests
.venv/bin/mypy src/repoweave
git diff --check
uv build --wheel --out-dir /tmp/repoweave-step9-wheel
```

Results: **410 offline tests passed, 1 live test deselected in 35.50 seconds**;
the explicitly selected **live test passed in 41.22 seconds**; Ruff passed;
mypy passed for **40 source files**; whitespace checking and the wheel build passed.
A fresh Python process outside the checkout loaded the wheel's packaged analyst
prompt and restrictive session configuration. All three role prompts were present
in the wheel and no `.env` file was included.

The live test authenticated with the local configuration and made two real calls
using invented measurements: an evidence-linked analyst diagnosis and bounded,
validated Zstd proposals. Each session passed the zero-enabled-tools and zero-MCP
checks before sending a prompt. No repository source or private data was sent.
The key was used only by the configuration loader and SDK, never printed or added
to command arguments. `.env` was not edited.

The 41 new offline cases cover strict schemas, measurement projection, bounded
candidate counts, evidence/parent IDs, dictionary registry/provenance checks,
partial/missing/malformed outputs, tool-policy rejection, unexpected execution
events, setup/transport failures, deadlines, cancellation, resource cleanup,
secret-safe errors, and unchanged policies after failure. Existing benchmark CLI
coverage now also validates conversion of a real report into sanitized evidence.

## Daytona snapshot preparation

Prepared the engine-only wheel build, digest-pinned Python Dockerfile, hashed
dependency requirements from `uv.lock`, and a five-file allowlisted upload context.
The image build runs a synthetic RPW/tar-zstd/tar-lzma reconstruction, replay, and
independent-decoder smoke test as an unprivileged user. See
[snapshot preparation and creation](daytona-snapshot.md).

Verified October 17, 2026:

```bash
.venv/bin/pytest tests/unit tests/integration -q
.venv/bin/pytest tests/unit/test_engine_image.py -q
.venv/bin/ruff check scripts/prepare_engine_image.py scripts/create_daytona_snapshot.py deploy/smoke_engine.py tests/unit/test_engine_image.py src/repoweave/execution/experiment_entrypoint.py src/repoweave/experiments/schemas.py
.venv/bin/mypy src/repoweave/experiments/schemas.py src/repoweave/execution/experiment_entrypoint.py scripts/prepare_engine_image.py scripts/create_daytona_snapshot.py deploy/smoke_engine.py
git diff --check
```

The full local suite passed **415 tests in 36.10 seconds**. After adding mocked
snapshot creation/reuse coverage, the focused file passed **6 tests in 1.12 seconds**.
The listed Ruff and mypy checks passed. The local wheel build passed and its
contents matched the engine source allowlist exactly.

After the configured key's permissions were corrected, the authorized retry on
October 18, 2026 created **`repoweave-engine-v1`**, confirmed **active** by Daytona.
Snapshot ID: `d8029be9-168f-4df7-91b1-2da0cc24e28c`; region `us`; 2 CPUs, 4 GiB RAM,
10 GiB disk, no GPU. The remote Dockerfile build completed its installed Linux
engine smoke tests and its check that agent/cloud packages are absent.
The reviewed build and successful creation receipt are under
`.repoweave/daytona-engine-v1`. Engine revision:
`64724fa58c228a90f6a7f93c5358126a3dbdad82f40fc8e2a50da8c9d573925e`.
No source dataset was uploaded and no sandbox was launched. Select the existing
snapshot when creating a sandbox; the remote build does not require filling the
Create Snapshot form's Image field.

## Live execution in the user-created sandbox

Verified October 18, 2026 in `repoweave-test-v1`, sandbox ID
`62722223-d28a-498c-b485-378c0ce4cd4c`, created by the user from the engine snapshot.
The live test checked 2 CPUs, 4 GiB RAM, 10 GiB disk, and disabled automatic deletion
before executing the preinstalled synthetic smoke test with a cleared environment.

```bash
env REPOWEAVE_LIVE_SANDBOX=repoweave-test-v1 .venv/bin/pytest -m live tests/live/test_daytona.py -q -s
.venv/bin/ruff check tests/live/test_daytona.py
git diff --check
```

Result: **1 live test passed in 4.89 seconds**; Ruff passed. The SDK emitted one
`server_url` deprecation warning. Remote exit status was zero; engine revision
matched; RPW, tar-zstd and tar-lzma reconstruction, completed-job replay, and a
decoder-only directory passed. No repository data or credentials were uploaded.
The test stopped and preserved the sandbox and confirmed its stopped state.
The result is saved under
`.repoweave/live-daytona/e59774fc53644445bb86bf48ece39c42/report.json`.

This was an existing-sandbox execution check. It did not test automatic provisioning,
upload/download of archive artifacts, a second clean decoder sandbox, or the complete
optimization workflow. No broad local regression rerun was needed for this opt-in test.

## Steps 10–12: optimization, LangSmith, and full cloud verification

Implemented October 18, 2026 on the existing locked environment: LangGraph 1.2.16,
LangSmith 0.14.11, and Daytona 0.225.0. LangSmith is now a direct dependency; no
resolved package versions changed. The engine revision remains
`64724fa58c228a90f6a7f93c5358126a3dbdad82f40fc8e2a50da8c9d573925e`, matching the existing
`repoweave-engine-v1` snapshot. No new image build was needed.

The backend now includes a reference-only checkpointed graph, immutable run/job
receipts, one-worker locking, cancellation, bounded proposals, approval interrupts,
CAS policy promotion, CLI run operations, verified cloud downloads, and a separate
clean decoder. LangSmith spans explicitly cover workflow nodes and Factory calls;
automatic state capture, runtime metadata, raw exceptions, source, and model prose
are excluded. Tests inspect the real SDK's outgoing bodies and persisted checkpoint
bytes for sentinel values. See [operation and recovery instructions](optimization.md).

Local verification commands actually run:

```bash
.venv/bin/pytest -q --tb=short
.venv/bin/pytest tests/unit/test_tracing.py tests/integration/test_optimization.py tests/unit/test_cli.py -q --tb=short
.venv/bin/ruff check src tests
.venv/bin/mypy src/repoweave
git diff --check
```

The last full regression passed **442 tests**, with **3 live tests deselected**, in
**47.30 seconds**. After the final trace-completion metadata/readback changes, the
focused command passed **36 tests in 11.78 seconds**; Ruff and mypy passed for
**59 source files**. The additional metadata-only live test was added after that
full regression; it is also excluded by default. Tests cover real engine round trips,
no gain, resource rejection, approval/restart/idempotency, stale promotion,
cancellation, uncertain creation, completed-job recovery, command/export failure,
corruption/size limits, cleanup retry and expiry, and trace/checkpoint redaction.

The authorized live commands were:

```bash
env REPOWEAVE_LIVE_CLOUD=1 .venv/bin/pytest -m live tests/live/test_cloud_optimization.py -q -s --tb=short
env REPOWEAVE_LIVE_CLOUD=1 REPOWEAVE_LIVE_CLOUD_RECHECK=f98be9d9a3d34ca38ac15d2b69f2364c .venv/bin/pytest -m live tests/live/test_cloud_optimization.py -q -s --tb=short
env REPOWEAVE_LIVE_CLOUD=1 .venv/bin/pytest -m live tests/live/test_langsmith.py -q -s --tb=short
```

The completed cloud run is
`d9f04c5201cbf2aebf6dbce751808d27466144bc28c074d83830a28371e87a94`.
It used a generated owned **68,048-byte** fixture, real Factory analyst/proposer
calls, one warmup plus two measured repetitions, and **12 distinct remote jobs**.
Every exported artifact passed request identity, hash, size, and local reconstruction
checks. Zstd level 9 produced **1,078 bytes**, equal to the level 3 RPW baseline, so
the outcome was **completed-without-improvement** and no policy was promoted.
The conventional tar-zstd/tar-lzma archives were **621/644 bytes** respectively;
these synthetic results do not establish RPW superiority.

The selected archive was uploaded alone into a second fresh sandbox. Its restored
archive was downloaded and compared byte-for-byte, including inventory and
executable bits, against the backend snapshot. Both sandboxes are confirmed deleted:
experiment `3c6d770f-14f8-4a3a-be71-23541f409cd0`, decoder
`bb5e61b9-cc83-4da8-b26f-0ad8d9d0d499`. They ran sequentially with 2 CPUs, 4 GiB RAM,
10 GiB disk, blocked outbound networking, and ten-minute lifetime limits.

Two earlier cloud attempts exposed the account's explicit-deletion failure and
asynchronous auto-deletion behavior. Cleanup now confirms actual absence after
stop/auto-delete instead of treating a stop response as deletion or propagating a
completed deletion as a failure. Their experiment sandboxes were also reconciled
and confirmed deleted. The first receipts remain preserved as failed attempts.

The completed run's initial immediate trace readback saw missing end timestamps.
A read-only recheck passed **1 test in 12.50 seconds**, confirming **9 completed
LangSmith spans**, including the Factory boundaries, with the source sentinel absent.
No sandboxes or model calls were repeated for that recheck. Live tests now retry
not-yet-indexed and incomplete traces within 45 seconds. Completion updates also
include the same start time, project, trace ID, and dotted order as creation.
The final separate metadata-only tracing test passed **1 test in 4.54 seconds**.
SDK deprecation warnings remain for Daytona's internal `server_url` fallback and
LangSmith's supported legacy `read_run` method.

Successful cloud receipt:
`.repoweave/live-cloud/f98be9d9a3d34ca38ac15d2b69f2364c/recheck-a7111aad559b490ab3c6a691c24257e7.json`.
Its sibling `state/` holds durable artifacts, jobs, and the returned decoder archive.
Successful final tracing receipt:
`.repoweave/live-tracing/55917cc98a5afca96abd24849d1fbca440a472db8c076b0ae7b1b93c1cd967ea.json`.
The local `.env` was neither printed nor changed. No checkout or private dataset was
uploaded, and no sandbox received backend credentials.

## Step 13 and extended cloud verification

Completed October 18, 2026. Added FastAPI 0.143.3 and direct locked dependencies
for Uvicorn 0.54.0 / HTTPX 0.28.1; existing resolved package versions were retained.
The deterministic engine revision remains unchanged:
`64724fa58c228a90f6a7f93c5358126a3dbdad82f40fc8e2a50da8c9d573925e`.

The localhost API now registers operator-configured fixture aliases, captures immutable
snapshots, persists idempotent pack/benchmark/optimize submissions, exposes actual
repository/workflow graphs, replays SSE progress, and supports approval, resume,
cancellation, metrics and authorized artifact downloads. A separate worker claims jobs
under an exclusive lock, executes existing engine/workflow operations, and preserves
state across API restarts. Dictionary requests reuse provenance validation, including
the extension control for graph-grouped benchmarks. Invalid browser paths, cross-origin
requests, symlinks, artifact tampering, and payload-bearing errors are rejected.
See [API usage and recovery](api.md).

Final verification commands and results:

```text
.venv/bin/pytest -q --tb=short
465 passed, 7 live tests deselected in 56.32 seconds

.venv/bin/pytest tests/integration/test_api.py tests/integration/test_optimization.py -q --tb=short
45 passed in 23.02 seconds (23 API/worker cases and 22 optimization cases)

.venv/bin/ruff check src tests
All checks passed

.venv/bin/mypy src/repoweave
Success: no issues found in 64 source files

git diff --check
Passed
```

API coverage includes real pack/benchmark/optimization work, dictionary assets and
controls, invalid paths and requests, concurrent duplicate submissions, sequenced SSE
replay, queued and active subprocess cancellation, checkpoint interruption/resume,
approval/CAS promotion, worker lock/recovery, a separate worker CLI process, artifact
access/tampering/symlinks, browser origin/host restrictions, sanitized failures, and
persistence across new API instances. The full suite includes the existing synthetic
local scale gate. A separately deployed/public API and dashboard were not tested.

The additional live command was:

```bash
env REPOWEAVE_LIVE_EXTENDED=1 .venv/bin/pytest -m live tests/live/test_extended_cloud.py -q -s --tb=short
```

**3 passed in 441.89 seconds.** The tests used the existing `repoweave-engine-v1`
snapshot, at most two sequential sandboxes per test, each 2 CPU / 4 GiB / 10 GiB and
a ten-minute lifetime. All **six** sandboxes were confirmed deleted. Downloads were
bounded and validated; each clean decoder received only its archive. Returned bytes,
inventory, and executable bits were checked locally. Public repository source was
never executed. Tracing and Factory calls were disabled in these tests; the earlier
real Factory/LangSmith checks remain recorded above.

### Larger public repositories

Pinned release downloads and their SHA-256 digests are in
`tests/live/test_extended_cloud.py`; raw downloads were staged under `/tmp` and inspected
as data. Tests cover supported regular files extracted from these releases, with the
engine's declared exclusions recorded in each receipt. Both runs used one warmup and
two measured repetitions per method, a 120-second limit for each encode/decode phase,
and a 1 GiB peak RSS eligibility limit. All measured archives reconstructed exactly
and had repeatable sizes.

| Public release | Supported files | Original bytes | RPW zstd | tar-zstd | tar-lzma |
| --- | ---: | ---: | ---: | ---: | ---: |
| Django 5.1.4 | 6,774 | 43,106,319 | 15,968,155 | 9,469,815 | 6,803,120 |
| SymPy 1.13.3 | 2,050 | 31,195,914 | 9,472,154 | 7,923,726 | 6,068,396 |

Django excludes `.github/workflows/data` and `tests/gis_tests/data`; SymPy excludes
`data` and `sympy/utilities/mathml/data`, under the existing documented inventory rules.
These are working-tree benchmarks, not Git-history archives. Conventional tar codecs
won these comparisons; no RPW size advantage is claimed.

Successful durable receipts:

- `.repoweave/live-extended/django-5.1.4-02af7d276e894f0a804ae275e96bc626/report.json`
- `.repoweave/live-extended/sympy-1.13.3-72467db44bc24a50a21bdcf50eb5c227/report.json`

### Live dictionary transfer and policy promotion

The third test used distinct generated training and validation repositories, a registered
256-byte extension dictionary, and a fixed proposer boundary to deterministically test
the requested cloud asset and promotion paths. The workflow, measurements, artifact
transfers, separate decoder, checkpoint restart, explicit approval, and CAS promotion
were real. No private data or backend credentials entered a sandbox.

The validation repository contained 50 files / 123,240 bytes. The stored parent archive
was **133,622 bytes**; the dictionary candidate was **18,281 bytes**, including the
256-byte embedded dictionary in each measured sample. The run paused at the real
approval checkpoint, was explicitly approved in its isolated test store, and promoted
exactly once. Repeated resume reused the existing jobs without new sandbox creation or
measurement execution. Plain RPW zstd was smaller still (**14,696 bytes**); the test
proves transfer/approval correctness, not a dictionary advantage over plain zstd.

Receipt:
`.repoweave/live-extended/dictionary-promotion-d2452619c0714ef98601e31dbcf6448b/report.json`.
Run: `c38130dabda213411c1098f1296e9027fccb68e161c686cc90b57fdb64eff993`.
The user's ordinary active policies were unaffected; all promotion state belongs to
this isolated test store. The existing Daytona SDK emitted its `server_url` deprecation
warning; all assertions passed.

## Remaining limits

The dashboard and neural codecs remain later steps. Complete private repositories,
the full 1 GiB archive limit, adversarial OS resource isolation, and cross-machine
performance remain unvalidated. Bounded approved private CSV subsets were subsequently
validated as recorded below. Public cloud coverage now includes the two releases above, plus
owned dictionary and promotion fixtures, on this account/snapshot/SDK combination.
RSS is a process-lifetime peak, not per-operation incremental memory. Abrupt worker
process death can leave bounded local children finishing; see the API recovery contract.
The default API remains local and single-user. The optional authenticated project
gateway was subsequently tested locally; a real public deployment remains untested.

Dataset lineage and whether a human consulted a final test report cannot be inferred
automatically. Dictionary selection now amortizes shared assets and checks exact total bytes; it
remains a conservative group filter rather than an exhaustive subset optimizer. Graph extraction supports the
documented static Python subset only. Factory's tool restrictions are not OS isolation;
use a trusted backend profile without custom lifecycle hooks. Factory may retain
approved measurement summaries in session history. No general compression advantage
is claimed. See [optimization](optimization.md), [graph behavior](repository-graph.md),
[benchmarks](benchmarks.md), and [archive formats](archive-format.md).


## Private datasets, hosted isolation, and compression follow-up (2026-10-18)

See [compression research](compression-research.md) for exact inputs, archive sizes,
training/evaluation separation, cloud snapshot versions, commands and durable receipts.
See [hosting](hosting.md) for the recommended Fly.io/Auth0 setup and its remaining gates.

- Two approved private CSV subset tests passed in Daytona: VIETA (23 files,
  16,064,788 bytes) and Portfolio (28 files, 1,514,294 bytes). Exact independent
  reconstruction succeeded and all four sandboxes were deleted. No private data went
  to Factory, LangSmith or public hosting. The original folders were not modified.
- The writer now compares dictionaries with plain zstd, amortizes asset costs across
  files, and falls back unless the full delivered archive is strictly smaller.
  Default plain archives and decoding formats remain compatible.
- A live same-level dictionary/promotion test passed on generated data with the new
  engine snapshot, independent decoding, exactly-once test promotion and two further
  deleted sandboxes. A persistent versioned engine snapshot was created; only the
  nonsecret snapshot-name setting in `.env` changed.
- Three public dictionary sizes trained on SymPy did not improve Django at level 3.
  A real Factory analyst/proposer then selected an 8 KiB dictionary at level 9;
  its local repeated benchmark passed. Conclusions retain the whole-tree baselines
  and distinguish level tuning from dictionary gains. The same-level Django dictionary
  gain is 25,912 bytes (0.17%) over plain RPW-9; whole-tree tar-zstd-9 remains smaller.
- Optional hosted mode verifies RS256 access tokens and configured project membership,
  separates every project's store and fixtures, bounds bodies/requests/queued jobs,
  and disables browser-authorized cloud transfers. The default localhost API stays
  unchanged. PyJWT with crypto is now a direct dependency at its existing locked
  resolution; no dependency package versions were upgraded.

Validation actually run:

```text
.venv/bin/pytest -q --tb=short
471 passed, 10 deselected in 64.14 seconds

.venv/bin/pytest tests/integration/test_hosting.py tests/integration/test_dictionary_archive.py -q --tb=short
15 passed in 1.95 seconds (after final project-path/case-collision checks)

.venv/bin/pytest tests/integration/test_api.py tests/integration/test_training_workflow.py tests/integration/test_hosting.py -q --tb=short
31 passed in 14.42 seconds

.venv/bin/pytest tests/integration/test_hosting.py -q --tb=short
6 passed in 3.33 seconds (includes the subsequently added real separate-worker test)

Private live test file: 2 passed in 110.17 seconds
Updated dictionary/promotion live test: 1 passed in 29.46 seconds
Factory public research live test: 1 passed in 166.93 seconds

.venv/bin/ruff check src tests scripts
All checks passed

.venv/bin/mypy src/repoweave
Success: no issues found in 66 source files

git diff --check
Passed
```

JWT verification tests use real ephemeral RSA signatures and a local signing-key
provider; no live Auth0 tenant, public TLS endpoint or Fly deployment was tested.
The user requested a hosting recommendation instead of supplying an account/target.
Actual public multi-user acceptance therefore remains pending its deployment and
identity configuration. Private testing covers the stated subsets, not all financial
files or complete private repositories. No final holdout or universal compression
superiority is claimed.
