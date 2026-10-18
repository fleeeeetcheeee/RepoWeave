# Steps 6–8.4 verification

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

## Remaining limits

Linux behavior, real large-repository corpora, the full 1 GiB archive limit,
adversarial OS resource isolation, and cross-machine timings remain unvalidated.
The scale gate covers only the two bounded synthetic workloads above. RSS is explicitly a
process-lifetime peak, not per-operation incremental memory. Dataset lineage and whether
a human has consulted a final test report cannot be inferred automatically. Dictionary
selection conservatively charges the first file the full embedded asset cost, so it can
miss gains amortized over many small files. Graph extraction supports the documented
static Python subset only. No graph/dictionary compression advantage is claimed.

Steps 9 onward, agent/cloud/live-service integrations, neural codecs, and the dashboard
remain unimplemented and untested. Nothing was pushed, published, provisioned, or sent
externally. See [graph behavior](repository-graph.md), [benchmarks](benchmarks.md), and
[archive formats](archive-format.md) for the implemented contracts.
