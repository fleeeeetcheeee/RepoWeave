# Bounded solid-block investigation — October 18, 2026

Subsequent work implemented and measured compressed/compact metadata and solid
LZMA on these exact fixtures and three public releases. See
[the metadata follow-up](metadata-research.md); the original V3 results below
remain the historical baseline.

Opt-in RPW V3 now compresses multiple whole files together in independent bounded
Zstd frames. This addresses cross-file redundancy without changing the codec or
requiring models, dictionaries, cloud services, or new dependencies. The existing
per-file encoding remains the default. [Usage](archive-usage.md#opt-in-solid-blocks)
and the [format contract](archive-format.md#v3-bounded-solid-blocks) specify the option.

The writer uses deterministic path order and compares complete V3 size against
same-level plain RPW. It emits V1 instead if solid encoding does not save bytes.
V1/V2 archives and existing policy identities remain compatible. An updated decoder
is required for V3. The shared policy field reaches pack, local benchmarks, the API
queue/worker, and experiment requests; generated frontend API types are updated.
The dashboard has no new block-size input. Cloud snapshots were not rebuilt.

## Local measurements

One captured snapshot per corpus; all candidates at Zstd level 3. The size sweep
tested plain RPW, 64 KiB, 1 MiB and 8 MiB targets and independently reconstructed
every result. The smallest candidate then ran one discarded warmup and two measured
repetitions against plain RPW, tar-zstd-3 and tar-lzma in fresh child processes.
All results were eligible, byte-exact, and size-repeatable. Limits were 120 seconds
per encode/decode and 1 GiB process-lifetime peak RSS. Timings exclude process startup;
RPW encoding includes comparison work and self-verification, and decoding includes
the restored inventory/bytes check. No test suite ran concurrently with the final
two measurement runs; filesystem caches and unrelated host activity were uncontrolled.

| Input | Files | Input bytes | Plain RPW | Solid 64 KiB | Solid 1 MiB | Solid 8 MiB | tar-zstd | tar-lzma |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| RepoWeave `src/repoweave` | 71 | 244,427 | 100,679 | 84,662 | 77,890 | 77,890 | 63,208 | 50,948 |
| Generated shared-content files | 257 | 1,049,600 | 1,104,120 | 134,722 | 68,667 | 64,513 | 6,528 | 6,108 |

The source corpus was the working implementation at measurement time, with snapshot
ID `ce130e0e7c142d9398f641da80eed61f4343f053e9d838a8a01d4577f49024c6`.
The generated workload contains 256 files, each the same 4,096 pseudorandom bytes
(`random.Random(2026).randbytes(4096)`) followed by its four-byte big-endian index,
plus an empty file and empty directory. It is intentionally favorable to cross-file
compression, not representative production data. Its snapshot ID is
`96a92059d5baf394af5900e9288b640d0205f0a71535c225c1eb1676002ee167`.

The chosen source policy used one 244,427-byte block with a 1 MiB target. The chosen
generated policy used one 1,049,600-byte block with an 8 MiB target. The 64 KiB
policies exercised four and eighteen blocks respectively. Identical source results
at 1 and 8 MiB occur because the full source corpus fits into one block in both cases.

| Input/method | Mean encode | Mean decode | Maximum observed process RSS |
| --- | ---: | ---: | ---: |
| Source plain RPW | 13.83 ms | 25.98 ms | 38,338,560 bytes |
| Source selected solid | 20.62 ms | 26.01 ms | 39,337,984 bytes |
| Generated plain RPW | 37.47 ms | 71.33 ms | 39,616,512 bytes |
| Generated selected solid | 57.23 ms | 71.38 ms | 43,220,992 bytes |

Host: macOS 27.0.1 arm64, Python 3.12.15, zstandard 0.25.0/libzstd 1.5.7,
Pydantic 2.14.1, existing `uv.lock`. These small, two-sample timing results describe
this host only. No speed improvement is claimed.

## What the measurements support

- Solid blocks reduced source archive size by **22.64%** and the generated archive
  by **94.16%** relative to same-level per-file RPW, including all archive overhead.
- Whole-tree tar codecs still won both comparisons. These are development inputs,
  not an untouched holdout, Django/SymPy rerun, or proof of general superiority.
- Uncompressed manifest overhead now matters substantially: 17,201 bytes of the
  77,890-byte source archive, and 58,450 bytes of the 64,513-byte generated archive.
  The source solid payload itself is 60,671 bytes; the remaining metadata and header
  largely explain its size gap to the 63,208-byte tar-zstd archive on this input.
- Larger blocks can exploit more shared content, but increase the amount decoded
  together. Targets are not universal optima. Files larger than the chosen target
  use singleton blocks, always within the existing hard 64 MiB file limit.
- Compression work increases because both alternatives are encoded. The fallback
  protects delivered size, not encoding time or temporary disk consumption.

Compressing or compacting manifest metadata and comparing broader public repository
families are evidence-backed follow-ups, not part of this change. Dictionary/solid
combinations, graph-based ordering, random-access extraction, and neural training
were not implemented.

## Reproduction and receipts

```bash
.venv/bin/python scripts/evaluate_solid_blocks.py src/repoweave .repoweave/solid-research-source-isolated
.venv/bin/python scripts/evaluate_solid_blocks.py .repoweave/solid-generated-input .repoweave/solid-research-generated
```

The generated source files were created locally using the recipe above. Choose new
output directories on reruns. Reports, immutable snapshots, policies, and selected
archives remain under those ignored directories. The initial source sweep in
`.repoweave/solid-research-source` ran alongside regression tests; its sizes matched,
but the isolated rerun supplies all source timings above. No private dataset was
read, no input source was executed as a workload, and no external service was called
for these compression runs.

## Verification

The initial focused archive/manifest/policy/dictionary suite passed **210 tests**.
The full offline suite then passed **521 tests, 10 live tests deselected**, in 63.68 s.
After adding explicit V3 benchmark-accounting and API queue/download tests, the
solid suite plus that API case passed **43 tests** in 3.36 s.

Final full verification passed **523 tests, 10 live tests deselected**, in 64.63 s.
Ruff and whitespace checks passed; mypy passed for 69 source/script files. API type
generation, strict TypeScript, and the production frontend build also passed.
Commands actually run:

```bash
.venv/bin/pytest tests/integration/test_solid_archive.py tests/unit/test_manifest.py tests/unit/test_policy.py tests/integration/test_archive.py tests/integration/test_dictionary_archive.py -q --tb=short
.venv/bin/pytest tests/integration/test_solid_archive.py tests/integration/test_api.py::test_solid_policy_survives_queue_and_archive_download -q --tb=short
.venv/bin/pytest -q --tb=short
.venv/bin/ruff check src tests scripts
.venv/bin/mypy src/repoweave scripts/evaluate_solid_blocks.py
git diff --check
cd web && npm run generate:api && npm run build
```

Coverage includes deterministic multiblock archives; exact boundaries and oversized
singleton files; stored blocks; empty files/directories; executable bits; Unicode
paths; credential-free decoding in a clean process; old policy serialization/IDs;
CLI/API routing; full byte accounting; no-gain V1 fallback; invalid references,
overlaps/gaps, unused blocks, paths, versions, hashes, truncation, concatenated
frames, and false decoded sizes. Existing V1/V2 compatibility and filesystem
publication tests also ran. The shared writer and decoder remain service-independent.

Live/cloud V3 execution, maximum 64 MiB block performance, full 1 GiB archives,
cross-platform runs, and broad unseen repository evaluation remain untested.
Browser end-to-end tests were not rerun; this change added no dashboard interaction.
