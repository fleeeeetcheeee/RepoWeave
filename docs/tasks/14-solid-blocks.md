Read AGENTS.md and DESIGN.md steps 6 and 8 before editing.
Investigate and implement opt-in bounded solid Zstd blocks using locked dependencies.
Specify RPW V3 before implementing it; preserve V1/V2 decoding and existing policy IDs.
Use deterministic path order, bounded independent blocks, per-file hashes, and safe
atomic publication. Reject malformed block references, layouts, and excessive output.
Keep a same-level per-file fallback based on full delivered archive size.
Expose the option through policies and existing CLI/API/benchmark callers; do not
combine dictionaries or graph grouping with solid blocks in this first implementation.
Test compatibility, independent decoding, corruption, boundaries, and policy routing.
Measure owned/generated local inputs against plain RPW and whole-tree baselines.
Do not read private datasets, execute input source, change dependencies, or deploy.
Report exact checks, measured results, and remaining limitations.
