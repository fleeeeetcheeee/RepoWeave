Read AGENTS.md, DESIGN.md steps 6/8, and the V3 archive contract.
Implement and measure compressed metadata, compact metadata, solid LZMA, then
broader public-repository comparisons, in that order. Retain every file's SHA-256,
exact supported bytes/metadata, decoder bounds, V1–V3 compatibility, and legacy
policy IDs/default output. Specify a versioned envelope before changing the decoder.
Reuse locked Zstd/LZMA dependencies; expose opt-in policy fields through existing
callers. Count full wire metadata and payload bytes, and retain smaller fallbacks.
Validate malformed metadata, expansion bounds, canonical binary records, block
references, and clean independent decoding. Run local same-snapshot, repeated,
fresh-process comparisons against tar-zstd and tar-lzma; include pinned Django,
SymPy and a public C repository. Inputs are data, never executed. Persist provenance
and exclusions. Do not read private datasets, upload source, change dependencies,
deploy cloud snapshots, or weaken integrity to improve the size comparison.
