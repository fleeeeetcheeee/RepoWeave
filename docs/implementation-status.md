# Steps 6–8 verification

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

## Remaining limits

Linux behavior, large-input performance, adversarial OS resource isolation, and
cross-machine timings have not been validated in this session. RSS is explicitly a
process-lifetime peak, not per-operation incremental memory. Dataset lineage and whether
a human has consulted a final test report cannot be inferred automatically. Dictionary
selection conservatively charges the first file the full embedded asset cost, so it can
miss gains amortized over many small files. Graph extraction supports the documented
static Python subset only. No graph/dictionary compression advantage is claimed.

Steps 9 onward, agent/cloud/live-service integrations, neural codecs, and the dashboard
remain unimplemented and untested. Nothing was pushed, published, provisioned, or sent
externally. See [graph behavior](repository-graph.md), [benchmarks](benchmarks.md), and
[archive formats](archive-format.md) for the implemented contracts.
