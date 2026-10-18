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
`--policy`. The baseline policy schema is the fixed subset from DESIGN.md step 8.1,
included here because the step 6 command requires it. Dictionary policies, grouping,
policy promotion/IDs, and benchmark work remain later steps.

Packing captures a version 2 snapshot under `REPOWEAVE_DATA_DIR/snapshots`. It encodes
only those copied bytes, checking their hash and executable mask. Compression is kept
only when encoded bytes plus that file's canonical metadata are smaller than stored
bytes plus stored metadata. Payloads are spooled to disk and files are processed one
at a time. The complete temporary archive is decoded and hash-checked before publication.
Identical snapshots with the same policy produce identical archives in the same locked
environment. Completed snapshots remain available after a failed pack.

Unpacking reads the [bounded V1 format](archive-format.md), decodes each file with its
recorded codec and parameters, checks its size/hash, and restores its executable bits.
It uses no application settings, service credentials, original repository, or active
policy. Output files use `0o600 | executable_bits`; directories use `0o700`. Other
permission bits and filesystem metadata are outside the declared scope.

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
