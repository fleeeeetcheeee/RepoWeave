Read AGENTS.md and DESIGN.md step 6.
Complete RPW V1 writing, reading, integrity verification, and pack/unpack/verify CLI commands.
Reuse version 2 snapshots, the manifest contract, and the explicit stored/zstd/lzma registry.
Encode only copied snapshot bytes. Count incremental manifest bytes when choosing stored fallback.
Bound reads and decompression, validate hashes, and reject malformed archives and unsafe paths.
Stage all output privately and publish atomically without replacing an existing destination.
Preserve source files and other operations' output on failure or interruption.
Support the documented baseline policy without implementing dictionary training or optimization.
Document verification scope and exclusions, platform limits, and failure behavior.
Test round trips, mixed codecs, metadata preservation, malformed input, races, and CLI integration.
Run pytest across tests/unit and tests/integration, Ruff on src/tests, and mypy on src/repoweave.
Do not add agents, cloud services, repository graphs, benchmarks, or dependency upgrades.
