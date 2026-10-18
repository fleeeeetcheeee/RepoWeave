# Metadata compression, compaction and solid LZMA — October 18, 2026

All four requested steps were implemented and measured locally. Compact Zstd RPW
beat the matched tar-zstd-3 baseline on the three tested public releases by
0.26–0.35%. This is a small size advantage with higher encode cost, not general
compression superiority. The original small source and generated fixtures still
lost to tar-zstd. Solid LZMA reduced RPW sizes further, but tar-lzma-6 remained
smaller on every input.

## Implementation

- RPW V4 stores metadata with an explicit representation/compression/decoded-length
  prefix. Stored/Zstd metadata is bounded to 16 MiB before and after decompression.
- `metadata_encoding: "compressed"` adds compressed canonical JSON as a candidate.
  `"compact"` also tries fixed-width binary records, maximal shared path prefixes,
  implied redundant fields and full binary SHA-256 digests. The smallest complete
  wire representation wins. No file hash or supported filesystem information was removed.
- Solid blocks now support LZMA/XZ using the existing codec and decoder bounds.
  The same-codec per-file fallback remains. Zstd uses level 3; LZMA uses preset 6
  in this study. Metadata compression uses Zstd level 3 in both cases.
- V1–V3 reading, default output and old policy identities are preserved. The CLI,
  API queue/worker, measurements and generated frontend schema carry the new field.
  New policy files are `policies/solid-compact-8m.json` and
  `policies/solid-lzma-compact-8m.json`. The dashboard has no new policy controls.
- No dependency versions, baseline policy, active policy, cloud snapshot or
  deployment changed. Older decoders need an update to read V4.

See the [format contract](archive-format.md#v4-compressedcompact-metadata-and-solid-lzma)
and [usage](archive-usage.md#compressed-metadata-and-solid-lzma).

## Full delivered archive sizes

All values are bytes, including the 18-byte header, wire metadata prefix,
metadata representation and encoded blocks. All stages used an 8 MiB block target.

| Input | Solid Zstd + JSON | + compressed JSON | + compact metadata | Solid LZMA + compact | tar-zstd-3 | tar-lzma-6 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Earlier RepoWeave source snapshot | 77,890 | 64,996 | 64,236 | 52,170 | 63,208 | 50,948 |
| Generated shared-content files | 64,513 | 16,621 | 15,530 | 14,429 | 6,528 | 6,108 |
| Django 5.1.4 | 11,115,563 | 9,537,218 | **9,436,930** | 7,084,049 | 9,469,815 | 6,803,120 |
| SymPy 1.13.3 | 8,329,023 | 7,925,379 | **7,899,990** | 6,194,674 | 7,923,726 | 6,068,396 |
| Zstd 1.5.7 C source | 2,310,554 | 2,206,374 | **2,197,749** | 1,798,759 | 2,203,391 | 1,786,656 |

Compressed JSON alone did not beat tar-zstd on any input. Compaction supplied the
last savings needed for the public releases: 32,885 bytes (0.3473%) on Django,
23,736 bytes (0.2996%) on SymPy, and 5,642 bytes (0.2561%) on Zstd. LZMA should be
compared with tar-lzma; its lower size relative to Zstd is not a container-only gain.

The source/generated inputs are the exact immutable inputs from the preceding
[solid-block study](solid-blocks.md). Their V3 and conventional control sizes match
the historical results. The prior in-memory source estimate of 64,990 bytes becomes
64,996 actual bytes after counting the new six-byte prefix.

Metadata explains most of the improvement. On Django, wire metadata fell from
1,955,495 bytes to 377,150 compressed JSON bytes and then 276,862 compact bytes;
the Zstd payload remained exactly 9,160,050 bytes in all three stages. On the small
source input, the corresponding metadata sizes are 17,201, 4,307 and 3,547 bytes.
The generated input still pays 9,467 compact metadata bytes, including 257 full
per-file hashes, against only 6,045 payload bytes. The tar controls do not carry
the same per-file SHA-256 index. Integrity was not weakened to improve the comparison.

## Timing and memory tradeoffs

Means below come from two measured samples after one discarded warmup. Peak RSS
is the maximum process-lifetime RSS across measured encode/decode children, not
incremental operation memory. RPW encoding includes both candidate layouts and
self-verification; every decode includes restored inventory/byte comparison.

| Public input / method | Encode seconds | Decode seconds | Peak RSS bytes |
| --- | ---: | ---: | ---: |
| Django compact Zstd RPW | 3.006 | 3.470 | 190,414,848 |
| Django tar-zstd | 1.227 | 3.245 | 76,857,344 |
| Django compact LZMA RPW | 11.867 | 3.632 | 280,788,992 |
| Django tar-lzma | 7.975 | 3.183 | 165,314,560 |
| SymPy compact Zstd RPW | 0.887 | 0.844 | 104,923,136 |
| SymPy tar-zstd | 0.372 | 0.812 | 48,955,392 |
| SymPy compact LZMA RPW | 8.145 | 1.038 | 211,910,656 |
| SymPy tar-lzma | 5.769 | 0.938 | 142,688,256 |
| Zstd compact Zstd RPW | 0.224 | 0.223 | 65,208,320 |
| Zstd tar-zstd | 0.101 | 0.201 | 44,695,552 |
| Zstd compact LZMA RPW | 2.048 | 0.269 | 166,903,808 |
| Zstd tar-lzma | 1.185 | 0.247 | 132,743,168 |

The small Zstd size wins cost roughly 2.2–2.5 times the tar-zstd encode time in
these runs. There is no speed or memory-efficiency win. All candidates and controls
passed the 120-second per-phase and 1 GiB measured RSS eligibility gates.

Measurements ran sequentially with fresh spawned encode/decode processes on macOS
27.0.1 arm64, Python 3.12.15, zstandard 0.25.0/libzstd 1.5.7 and Pydantic 2.14.1,
using the existing lockfile. Test suites and builds did not run concurrently with
the benchmark sequence. Caches were not flushed, CPUs were not pinned, and unrelated
host activity was uncontrolled. Reports retain raw samples and timing variances.

## Corpus scope and provenance

| Input | Supported files | Included bytes | Actual blocks |
| --- | ---: | ---: | ---: |
| Source snapshot | 71 | 244,427 | 1 |
| Generated fixture | 257 | 1,049,600 | 1 |
| Django | 6,774 | 43,106,319 | 6 |
| SymPy | 2,050 | 31,195,914 | 4 |
| Zstd | 560 | 7,840,774 | 1 |

The public input archives are pinned and SHA-256 checked before bounded extraction:

- [Django 5.1.4](https://codeload.github.com/django/django/tar.gz/refs/tags/5.1.4):
  `adbb3c6702033d417401ce1925f7307b70ee2d8bfca636c82c66a38c55b97dda`.
- [SymPy 1.13.3](https://codeload.github.com/sympy/sympy/tar.gz/refs/tags/sympy-1.13.3):
  `3cfd34ee7393c3786356b8ce49c3d6d212960d4c87c61d47e29a381cb5f41f58`.
- [Zstd 1.5.7](https://codeload.github.com/facebook/zstd/tar.gz/refs/tags/v1.5.7):
  `37d7284556b20954e56e1ca85b80226768902e2edabd3b649e9e72c0c9012ee3`.

Four Django links/special members and two Zstd links/special members were skipped
during staging; SymPy had none. Inventory then excluded Django's
`.github/workflows/data` and `tests/gis_tests/data`, SymPy's `data` and
`sympy/utilities/mathml/data`, and Zstd's `build`. Thus these are supported working-tree
subsets, not complete Git histories or every release file. Empty directories and
executable bits are retained. All methods consumed the same snapshot for each input.

No downloaded source was executed. No private dataset was read, no source was
uploaded, and no model/cloud calls were made. The Zstd release download required
network permission; the Django and SymPy release archives were reused from the
previous approved public downloads.

## Reproduction and receipts

```bash
.venv/bin/python scripts/prepare_public_benchmarks.py django-5.1.4 /private/tmp/repoweave-django-5.1.4.tar.gz .repoweave/metadata-study/public/django-5.1.4
.venv/bin/python scripts/prepare_public_benchmarks.py sympy-1.13.3 /private/tmp/repoweave-sympy-1.13.3.tar.gz .repoweave/metadata-study/public/sympy-1.13.3
.venv/bin/python scripts/prepare_public_benchmarks.py zstd-1.5.7 /private/tmp/repoweave-zstd-1.5.7.tar.gz .repoweave/metadata-study/public/zstd-1.5.7
.venv/bin/python scripts/evaluate_archive_formats.py SOURCE NEW_OUTPUT_DIRECTORY
```

The five completed output directories are
`.repoweave/metadata-study/results/{source,generated,django,sympy,zstd}/`.
Each retains `report.json`, four detailed stage JSON reports, four verified RPW
archives, policies and its immutable input snapshot. Public staging provenance
is in `.repoweave/metadata-study/public/RELEASE/provenance.json`. Choose fresh output
directories on reruns. The source input was the earlier capture beneath
`.repoweave/solid-research-source-isolated/captures/`; the generated input was
`.repoweave/solid-generated-input`. Public inputs are the staged `source/` directories.

Every stage measured all four methods with one warmup and two repetitions. Across
five inputs this is 160 measured encode/decode pairs, plus 80 warmups. Every pair
reconstructed supported bytes/inventory/executable bits exactly; all measured
archive sizes repeated. Retained candidates were separately self-verified and
matched the measured sizes. No policies were promoted.

## Verification and remaining limits

Focused metadata/solid/API tests passed 81 cases before the final coverage additions.
The first full offline regression passed 561 tests with 10 live tests deselected;
the public preparation helper's six tests also passed. The final full regression
passed **574 tests, 10 live tests deselected, in 67.71 seconds**. Ruff, mypy
(72 source/script files), generated API types, the strict TypeScript/production
build and whitespace checks passed. Exact commands are recorded in
[implementation verification](implementation-status.md).

Coverage includes V1–V3 regression, V4 stored/compressed/compact metadata,
same-input deterministic output, metadata expansion limits, invalid/truncated
records, canonical prefix encoding including UTF-8 byte boundaries, unknown
codecs/representations, payload bounds, full hashes, LZMA blocks, dictionary archives
with compressed JSON, clean credential-free decoding, policy IDs, API downloads,
and bounded public staging with digest/path/link checks.

These are exploratory development results, not a final held-out evaluation.
Three public projects and two owned fixtures do not establish general superiority.
Live/cloud V4 execution, cross-platform behavior, maximum 64 MiB block performance,
full 1 GiB archives and browser end-to-end flows were not tested in this follow-up.
The unchanged decoder limits remain format bounds, not an OS memory sandbox.
