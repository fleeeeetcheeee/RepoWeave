# Local archive commands

Install the locked environment with `uv sync --locked`, then run from the project root:

```bash
uv run repoweave pack examples/demo_repository -o .repoweave/demo.rpw --policy policies/baseline.json
uv run repoweave unpack .repoweave/demo.rpw -o .repoweave/restored
uv run repoweave verify examples/demo_repository .repoweave/restored
```

Choose new output names on repeat runs: existing archives and extraction directories
are never replaced, including empty directories and dangling symlinks. `pack` also
accepts `--codec stored`, `--codec zstd` (the default), or `--codec lzma` instead of
`--policy`. Policies now have content IDs and optional trained dictionary bundles;
see [benchmarks and dictionaries](benchmarks.md). Active-policy promotion remains a
later workflow step. The committed baseline is unchanged.

Packing captures a version 2 snapshot under `REPOWEAVE_DATA_DIR/snapshots`. It encodes
only those copied bytes, checking their hash and executable mask. Compression is kept
only when encoded bytes plus that file's canonical metadata are smaller than stored
bytes plus stored metadata. Payloads are spooled to disk and files are processed one
at a time. The complete temporary archive is decoded and hash-checked before publication.
Identical snapshots with the same policy produce identical archives in the same locked
environment. Completed snapshots remain available after a failed pack.

Unpacking reads the [bounded V1–V4 formats](archive-format.md), decodes each file with its
recorded codec and parameters, checks its size/hash, and restores its executable bits.
It uses no application settings, service credentials, original repository, or active
policy. Output files use `0o600 | executable_bits`; directories use `0o700`. Other
permission bits and filesystem metadata are outside the declared scope.

## Opt-in solid blocks

The included `policies/solid-1m.json` opts into a 1 MiB block target:

```json
{"schema_version":1,"name":"solid-1m","codec":"zstd","zstd_level":3,"solid_block_bytes":1048576}
```

```bash
uv run --locked repoweave pack examples/demo_repository --policy policies/solid-1m.json -o .repoweave/solid.rpw
uv run --locked repoweave unpack .repoweave/solid.rpw -o .repoweave/solid-restored
uv run --locked repoweave benchmark examples/demo_repository --policy policies/solid-1m.json --output .repoweave/solid-benchmark.json
```

The same optional field is accepted in API policies. The dashboard does not yet
offer a block-size control. Files share a compression frame in sorted path order;
the decoder retains one block at a time and verifies individual file hashes.
Targets range from 1 byte to 64 MiB. Files larger than the target occupy a block
alone, up to the existing 64 MiB file limit. Dictionary policies cannot use this
option. Whole files are never split across blocks.

The writer measures both solid and ordinary same-level per-file archives and keeps
solid only when the complete archive is smaller. Tiny/incompressible inputs can
therefore still produce V1. This adds encoding work and scratch disk use; it is
not a speed optimization. V3 archives need an updated decoder. No existing cloud
snapshot was updated by this feature.

## Compressed metadata and solid LZMA

`metadata_encoding` accepts `json` (legacy default), `compressed` (also try Zstd
compression of canonical JSON), or `compact` (also try prefix-compressed paths,
fixed-width binary records and binary SHA-256 hashes, with optional Zstd compression).
The smallest permitted wire representation wins, including framing. Per-file
hashes, paths and executable bits are retained; compact mode cannot use dictionaries.
Choosing compact mode does not guarantee binary metadata if compressed JSON is smaller.

Solid policies also accept `codec: "lzma"`, using the existing XZ preset 6 and
decoder memory bounds. Payload LZMA and metadata Zstd are independent choices.
The same-codec per-file fallback remains, and no existing default policy changes.
V4 requires the updated reader; V1–V3 decoding remains supported.

```bash
uv run --locked repoweave pack owned-input --policy policies/solid-compact-8m.json -o .repoweave/compact.rpw
uv run --locked repoweave pack owned-input --policy policies/solid-lzma-compact-8m.json -o .repoweave/compact-lzma.rpw
uv run --locked python scripts/evaluate_archive_formats.py owned-input .repoweave/format-study
```

The two included policies use an 8 MiB uncompressed target. Files exceeding it use
singleton blocks up to 64 MiB. LZMA generally warrants more encode time/memory;
inspect the measured report rather than selecting solely on bytes. The dashboard
does not yet expose these policy controls. CLI/API submissions support them.

## Verification scope

`verify` takes fresh snapshots of both directories and compares all supported paths,
file bytes, executable bits, and empty directories. Any missing/extra supported entry,
different bytes, or changed executable mask returns nonzero. It compares actual bytes
after the inventory/hash comparison. Temporary verification snapshots are cleaned up.

Both roots use the [inventory exclusions](snapshot-interface.md#exclusions): secret/build
names, repository metadata, symlinks, and special files are outside the supported scope.
Successful `pack` and `verify` print exclusions; no file bodies are printed. Verification
ignores the configured data directory when it lies inside either root, and ignores the
restored directory when it is nested inside the original. Excluded files are not verified.

Packing excludes its output directory if nested inside the source (or only the archive
file if directly in the source root). For a custom location, give verification the same
relative exclusion explicitly; archives contain reconstruction metadata, not local output paths:

```bash
uv run repoweave pack owned-input -o owned-input/reports/demo.rpw
uv run repoweave unpack owned-input/reports/demo.rpw -o restored
uv run repoweave verify owned-input restored --exclude reports
```

`--exclude` can be repeated and applies to both roots. Prefer outputs beneath the default
`.repoweave` directory to avoid needing custom exclusions. Unsupported archive filenames
(including non-NFC spelling and Windows device names) fail packing; they are not renamed
or silently dropped. Snapshots of such names remain usable for analysis.

## Filesystem safety and limitations

Archive input/output access and extraction use directory descriptors with no-follow opens.
Symlinks in user-specified archive/output paths or their ancestors are rejected. Use real
paths (for example `/private/tmp` rather than the macOS `/tmp` symlink). Staging directories
are private and live beside the output on the same filesystem. Publication uses exclusive
rename: [macOS RENAME_EXCL](https://developer.apple.com/documentation/foundation/urlresourcevalues/volumesupportsexclusiverenaming)
or [Linux RENAME_NOREPLACE](https://man7.org/linux/man-pages/man2/renameat2.2.html).
Unsupported systems/filesystems fail safely without a replacing-rename fallback.

Caught errors and interrupts remove only this operation's staging files. SIGKILL or a
power loss can leave a hidden `.repoweave-*` staging directory; existing published output
is preserved. Files are flushed and fsynced before publication, but directory fsync and
full power-loss durability are not promised. Source capture is not a filesystem-wide
transaction; stop writers when several input files must reflect one moment.

The V1 size/window limits bound individual operations, not total process memory, encoder
CPU, or available disk space. Large-input performance and cross-platform filesystem behavior
need validation in the deployment environment. SHA-256 detects corruption; archives are
not authenticated against a malicious sender who rewrites both content and hashes.
