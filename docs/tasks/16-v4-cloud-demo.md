# V4 cloud showcase

Read DESIGN.md steps 12–14 and the V4 archive contract. Update the existing demo,
engine-only snapshot smoke checks, and bounded agent policies for compressed/compact
metadata and solid Zstd/LZMA. Preserve the showcase: remote compression, a second
fresh decoder receiving only the archive, then local exact-byte verification.

The user explicitly authorized a new versioned Daytona engine snapshot and live
tests with the owned synthetic demo fixture. Upload only the reviewed engine build
context and that fixture; keep credentials in the backend. Reuse uv.lock, preserve
the old snapshot, run at most two sequential sandboxes per test run with the existing
2 CPU/4 GiB/10 GiB/ten-minute bounds, and confirm deletion. Test all three V4 modes
deterministically and the browser workflow. Record receipts and exact checks; do not
claim broader compression superiority or upload private/public repository datasets.
