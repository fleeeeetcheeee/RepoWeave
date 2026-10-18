# Local benchmarks and dictionaries

Steps 7–8 run locally with the locked engine dependencies and no service credentials.
Only use owned or explicitly approved repositories. Input source is never executed.

The local scale gate in [DESIGN step 8.4](../DESIGN.md#84-validate-larger-repositories-locally-before-adding-agents)
runs with `uv run --locked pytest tests/integration/test_large_repositories.py` and is
also included in the ordinary unit/integration suite. It generates 2,114-file and
128 MiB synthetic workloads in pytest temporary directories, with no downloads or
credentials. Each test leaves `analysis.json` and `benchmark.json` there under pytest's
normal retention policy; intermediate benchmark workspaces are removed. Allow roughly
2 GiB of temporary disk space per run, plus retained older runs. These are bounded
correctness/resource regressions, not real-corpus compression research.

## Baseline measurements

```bash
uv run --locked repoweave benchmark examples/demo_repository \
  --policy policies/baseline.json --output .repoweave/baseline-report.json
```

The command captures one snapshot and passes that same snapshot to every method:
the selected RPW policy, plain per-file RPW zstd level 3, normalized whole-tree
tar+zstd level 3, and tar+lzma preset 6. Tar uses stable entry order, zero timestamps,
zero UID/GID, empty owner names, normalized modes, and preserves empty directories
and executable bits. Its decoder verifies entries against the captured inventory,
never calls `extractall`, and compares restored bytes. No Git-history packing is included.

The default is one discarded warmup and three measured repetitions per method.
Use `--warmups 0` for no warmup; repetitions must be 2–20. Every encode and every
decode runs in a fresh spawned child, sequentially. Process startup is excluded from
`perf_counter` timing. RPW encoding includes the writer's self-verification; dictionary
policy encoding also includes local asset lookup and grouping. All decode timings
include exact inventory, executable-bit, and byte comparison. Caches are not flushed;
the host is not CPU-pinned, and other machine activity is uncontrolled.

Reports contain raw samples, timing means and sample variances, original included
bytes, full delivered archive bytes, embedded dictionary bytes, manifest/payload
bytes for RPW, declared dictionary hashes, zero external dependency bytes, reconstruction
status, snapshot/exclusions, policy IDs, Python/package/libzstd versions, and platform/CPU
information. Base64 dictionary storage is part of manifest bytes and is counted in the
full archive measurement; decoded dictionary byte counts are informational, not added
again. Tar metadata is compressed inside its full archive size.

Peak memory is `resource.getrusage(RUSAGE_SELF).ru_maxrss`: a **process-lifetime maximum**
including imports and library initialization, not incremental per-operation allocation.
macOS reports bytes; Linux reports KiB, converted to bytes. Other platforms are not
supported by this implementation. The runner uses explicit limits:

```bash
uv run --locked repoweave benchmark examples/demo_repository \
  --policy policies/baseline.json --output .repoweave/constrained-report.json \
  --max-encode-seconds 30 --max-decode-seconds 30 --max-memory-bytes 536870912
```

Defaults are 300 seconds per encode/decode child and 1 GiB measured peak RSS. Child
wall-clock timeouts include startup and terminate the child. The memory limit is a
measurement eligibility gate, not an OS memory sandbox. Failed codecs, missing assets,
timeouts, byte mismatches, invalid metrics, differing archive sizes across repetitions,
and over-limit results receive no score. Among eligible results, the objective is minimum
**total archive bytes**. No weighted score or compression superiority claim is made.
The CLI can successfully publish a report containing failed candidates; inspect each
result's status and eligibility. Errors contain failure classes, never exception bodies.

Reports and policy artifacts publish exclusively; choose a fresh report path on reruns.
The captured snapshot remains available. Temporary measurement archives and restores
are removed at the end, including on ordinary failures; use `pack` to retain a deliverable.
SIGKILL can leave temporary work directories. Unpartitioned measurements are labeled
`unpartitioned-exploratory` and are not final holdout evidence.

## Repository partitions and training

Create a local split manifest with **stable repository IDs**, not individual file names:

```json
{"schema_version":1,"train":["training-repo"],"validation":["validation-repo"],"test":["heldout-repo"],"test_retired":false}
```

Each ID may occur only once across all three partitions. Assign different revisions,
copies, or forks of the same underlying repository to the same partition; the tool
cannot infer repository lineage from arbitrary local folders. Identical training
snapshots cannot be registered under two training IDs. Evaluation also rejects the
exact training snapshot under any alias and rejects training IDs even when bytes change.

```bash
uv run --locked repoweave train-dictionaries --splits .repoweave/splits.json \
  --repository training-repo=/absolute/path/to/approved-training-repo \
  --grouping extension --dictionary-size 8192
```

Repeat `--repository ID=PATH` for multiple designated training repositories. Only these
folders are captured; validation/test paths are not needed or read during training.
Train `none`, `extension`, or `graph` grouping separately. Training is bounded to 64 MiB
of nonempty file samples, 64 dictionary groups, 1 MiB per dictionary, and a 16 MiB bundle.
The bundle stores repository/snapshot IDs, per-file sample hashes and paths, grouping,
requested dictionary size, trainer/version information, dictionary bytes and SHA-256.
Its ID hashes its canonical validated JSON content. Bundles live under
`REPOWEAVE_DATA_DIR/dictionaries/<id>.json` and are never overwritten.

Insufficient samples may cause zstd training to fail. Each failed group is recorded as
`training-failed`; encoding that group, or a group absent from training, explicitly falls
back to ordinary zstd (which may itself choose stored bytes). Packing prints missing/failed
groups, and benchmark reports record them. No fallback invents a trained dictionary.

Dictionary bundles contain source-derived bytes. Keep them local with the same access
restrictions as their approved training inputs; they are not metadata-only reports.

## Versioned policies and graph controls

Keep the committed `policies/baseline.json` unchanged. Save a candidate JSON under
`.repoweave/` with the printed dictionary bundle ID:

```json
{"schema_version":1,"name":"extension-dictionary","codec":"zstd","zstd_level":3,"dictionary_id":"REPLACE_WITH_64_HEX_BUNDLE_ID","grouping":"extension"}
```

The placeholder above must be replaced before validation. Dictionary policies require
zstd and matching bundle grouping. Policy IDs are SHA-256 of canonical validated content;
packing and benchmarking persist immutable versions under `.repoweave/policies/<id>.json`.
There is no active-policy promotion command until the workflow steps. Policies select
encoding only; unpacking needs neither the policy registry nor a training bundle.

```bash
uv run --locked repoweave pack /absolute/path/to/approved-validation-repo \
  --policy .repoweave/extension.json -o .repoweave/dictionary.rpw
uv run --locked repoweave unpack .repoweave/dictionary.rpw -o .repoweave/restored
```

For a graph comparison, train graph and extension bundles from the same training
snapshots, create corresponding policy files, then run:

```bash
uv run --locked repoweave benchmark /absolute/path/to/approved-validation-repo \
  --policy .repoweave/graph.json --control-policy .repoweave/extension.json \
  --splits .repoweave/splits.json --repository-id validation-repo \
  --output .repoweave/graph-comparison.json
```

Graph policies require an extension control with the same codec/level and training
repository snapshots. Both are compared on the exact same captured evaluation bytes,
alongside the plain RPW and whole-tree baselines. Different dictionary sizes are visible
in bundle provenance; use the same size when isolating the grouping effect.

Use validation results during search. A final test report additionally requires
`--partition test --final` and a test repository ID in the split manifest. No optimization
or policy promotion occurs here. If test results inform another search, mark the split
manifest `test_retired: true`, choose a new holdout, and retrain/version bundles under the
new split declaration. Retired tests are refused as final holdouts. The tool cannot
prevent a person from consulting a report; this remains a dataset-governance obligation.
