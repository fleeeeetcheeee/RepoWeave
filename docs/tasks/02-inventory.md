Read AGENTS.md and DESIGN.md step 5.
Implement only local repository inventory, immutable copied snapshots, and CLI analysis.

Allowed scope: src/repoweave/repository/{__init__,paths,inventory,snapshot}.py,
src/repoweave/cli.py, focused tests/fixtures, and docs/snapshot-interface.md.
Keep the existing CLI/configuration behavior and dependency lockfile.

Provide create_snapshot(input_root, snapshots_dir, *, exclude_paths=()) returning
snapshot_id, files_root, sorted entries, and exclusions. Capture regular files,
directories (including empty ones), file bytes, and executable status. Hash copied
bytes, never reopen original files for downstream encoding. Reject unsafe paths;
report secret/build/output exclusions, symlinks, and special files. Fail explicitly
on unreadable supported inputs. Publish only complete snapshots and preserve old data.

Add repoweave analyze INPUT --output REPORT.json, using REPOWEAVE_DATA_DIR for
snapshot storage. Reports must be stable metadata-only JSON, with scope/exclusions.
Document the interface, naming-based secret exclusions, and capture limitations.

Test exact copied bytes, deterministic identity/order, changed contents/metadata,
independence from later source changes, unsafe paths, exclusions, symlink replacement,
unreadable files, failed-copy cleanup, and CLI integration without credentials.
Use only tiny synthetic repositories; never execute their source files.

Run uv run --locked pytest tests/unit and uv run --locked ruff check src tests.
Run uv run --locked mypy src/repoweave for the new typed interface.
Report exact checks and remaining limitations. Do not implement archives, graphs,
agents, cloud services, or dependency upgrades.
