# Steps 6–9 verification

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

## Remaining limits

Linux behavior, real large-repository corpora, the full 1 GiB archive limit,
adversarial OS resource isolation, and cross-machine timings remain unvalidated.
The scale gate covers only the two bounded synthetic workloads above. RSS is explicitly a
process-lifetime peak, not per-operation incremental memory. Dataset lineage and whether
a human has consulted a final test report cannot be inferred automatically. Dictionary
selection conservatively charges the first file the full embedded asset cost, so it can
miss gains amortized over many small files. Graph extraction supports the documented
static Python subset only. No graph/dictionary compression advantage is claimed.

Steps 10 onward, cloud experiment execution, neural codecs, and the dashboard remain
unimplemented and untested. Live Factory verification covers only synthetic reports
on this installed SDK/CLI and account; no actual optimization or compression gain
was measured. The CLI policy is not OS isolation: use a trusted backend profile
without custom lifecycle hooks. Factory can retain approved summaries in its own
session history. Nothing was pushed, published, or provisioned. See
[graph behavior](repository-graph.md), [benchmarks](benchmarks.md), and
[archive formats](archive-format.md) for the implemented contracts.
