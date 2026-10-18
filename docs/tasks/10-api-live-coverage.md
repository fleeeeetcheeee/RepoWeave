Read AGENTS.md and DESIGN.md steps 8.4, 10, 12, and 13.
Implement Step 13: localhost-only FastAPI, explicitly configured fixture aliases,
immutable registration, persistent SQLite submissions, one separate worker, pack,
benchmark and optimize using existing operations, idempotency, status/artifacts,
SSE replay, approval/resume/cancellation, and actual workflow topology.
Test transitions, invalid paths, duplicate submission, replay, active cancellation,
artifact authorization, API/worker restart and completed-run persistence.
The user also requests the three previously missing live checks: larger public
repositories, dictionary transfer, and approved promotion. Use pinned public release
archives (Django 5.1.4 and SymPy 1.13.3), never execute their source; record downloads,
hashes and exclusions. Use generated owned training/validation fixtures for dictionary
transfer and a measured improvement from a stored baseline for approval/promotion in
an isolated state directory. Reuse the existing engine snapshot; max two sequential
sandboxes per run, 2 CPU/4 GiB/10 GiB/ten minutes each. Confirm cleanup. No private
repositories, credentials, checkout uploads, new images, publishing, or dashboard.
Explicit request authorizes these bounded test resources and public/generated data.
