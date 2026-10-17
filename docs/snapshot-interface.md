# Repository snapshots (step 5)

```python
from pathlib import Path
from repoweave.repository import create_snapshot

snapshot = create_snapshot(Path("owned-input"), Path(".repoweave/snapshots"))
```

The returned frozen `Snapshot` has `snapshot_id`, `files_root`, `entries`, and
`exclusions`. Entries and exclusions are tuples sorted by relative path. Consumers
read `snapshot.files_root / entry.path`; never reopen the original repository.
Repository capture uses only the standard library and has no configuration, agent,
cloud, or source-execution side effects.

## Entries and identity

A file entry contains `path`, `kind="file"`, `size_bytes`, `sha256`, and a boolean
`executable`. A directory entry contains only `path` and `kind="directory"` when
serialized with `to_dict()`. The root itself is implicit; empty directories are
preserved. Paths use `/`, retain Unicode spelling, and cannot be absolute, contain
empty/`.`/`..` components, backslashes, colons, control characters, or invalid UTF-8.
Destination collisions fail capture instead of overwriting copied files.

The supported scope is `regular-files-directories-executable-v1`: file bytes,
relative paths, directories, and whether any file executable bit was set. Exact
permission masks, directory permissions, ownership, timestamps, extended attributes,
hard-link relationships, symlinks, and special files are not preserved.

`snapshot_id` is the lowercase SHA-256 hex digest of UTF-8 JSON with this content:

```json
{"schema_version":1,"scope":"regular-files-directories-executable-v1","entries":[]}
```

Replace `entries` with the sorted serialized entries, then serialize using
`sort_keys=True`, `separators=(",", ":")`, and `ensure_ascii=True`. There is no trailing
newline in the hashed input. Each file hash describes the bytes actually copied.
Storage paths, capture times, and exclusions do not participate in content identity.

## Storage and failures

Every capture gets a new owned directory beneath `snapshots_dir`, containing `files/`
and `report.json`. A temporary directory is renamed into place only after copying and
report creation succeed. Identical captures share an identity but currently occupy
separate directories. Existing snapshots are never replaced or automatically deleted.

Copies are independent of source files, and copied files have their write bits removed.
Consumers must treat the entire snapshot directory as immutable; the local file owner
can still deliberately modify it. This is not tamper-proof storage.

Traversal and file reads use directory descriptors and no-follow opens. Symlink or
special-file replacements cannot redirect a pending read. Unreadable supported files,
unsafe paths, copy failures, and detected changes during an individual file copy raise
`RepositoryError`; partial capture directories are cleaned up. No partial snapshot is
returned. Capture is supported on macOS/Linux and fails explicitly elsewhere.

Capture does not provide a filesystem-wide transaction: separate files can reflect
different moments if the repository is being edited. Stop writers when a mutually
consistent view of several files is required. Individual files are streamed in 1 MiB
chunks, with size and modification metadata checked before/after copying.

## Exclusions

Exclusions have `path` and `reason`. Excluded directories are reported once without
descending into them; redundant descendant exclusions are omitted. Default names
are matched case-insensitively and skip:

- `.git`, `.venv`, `venv`, `node_modules`, `build`, `dist`, `__pycache__`,
  `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.repoweave`, `data`, `datasets`, `artifacts`.
- Secret names, matched case-insensitively: `.env`, `.env.*` (including templates),
  `.aws`, `.ssh`, `.gnupg`, `.netrc`, `.npmrc`, `.pypirc`, `credentials`, `credentials.*`,
  `secrets`, `secrets.*`, `id_rsa*`, `id_ed25519*`, `id_ecdsa*`, `id_dsa*`,
  `*.pem`, `*.key`, `*.p12`, and `*.pfx`.
- Symlinks and special files, including FIFOs and sockets.
- Snapshot storage and explicit `exclude_paths`, which may name files or directories.

Explicit exclusions are reported even when absent, so creating an output file does
not change its exclusion record. Paths outside the input are irrelevant. An exclusion
containing the entire input is rejected. `.gitignore` is not interpreted in V1.
Name-based rules cannot identify credentials or private data hidden in arbitrary files;
capture only approved inputs. No file content is scanned or emitted in reports.

## CLI and reports

```bash
uv run --locked repoweave analyze owned-input --output .repoweave/analysis.json
```

The CLI stores captures under `REPOWEAVE_DATA_DIR/snapshots` (default `.repoweave`).
It excludes the configured data directory and, when inside the input, the report's
output directory. If the report is directly in the input root, only the report file
is excluded. Choose a dedicated output directory, since all its contents are excluded.

`snapshot.to_report()` returns deterministic JSON-compatible metadata: schema version,
scope, identity, entries, exclusions, file count, and total included bytes. It contains
no source bodies, absolute input paths, timestamps, or storage location. The CLI prints
the snapshot identity and local `files_root` separately so consumers can locate it.
The same report is persisted alongside the snapshot.

Reports are published atomically without overwriting any existing destination. Choose
a fresh report filename for another run. A report-write failure leaves any completed
snapshot intact and returns a nonzero exit status. Local analysis needs no service keys.
