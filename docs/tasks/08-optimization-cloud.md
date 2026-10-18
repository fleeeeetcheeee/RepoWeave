Read AGENTS.md and DESIGN.md steps 10 and 12.
Implement the LangGraph optimization workflow, SQLite run/job/event persistence,
bounded local and Daytona experiments, approval/CAS promotion, recovery and cancellation,
CLI operations, and the shared engine-only experiment entrypoint and image recipe.
Keep engine and decoding independent of Factory/LangGraph/Daytona. Disable external
tracing. Stage only explicitly approved owned snapshots and registered assets.
Use at most two sequential Daytona sandboxes per run, each at most 2 CPUs, 4 GiB RAM,
10 GiB disk, and ten minutes. Persist creation intent and sandbox IDs; export and
verify artifacts, independently decode, and reconcile cleanup after interruption.
Test recovery, idempotency, failure, rejection, budgets, cancellation and promotion
with fake agents and mocked Daytona, plus real engine round trips. Run the full suite,
Ruff and mypy. Live cloud validation needs the user's configured key/snapshot and
explicit approval for the selected fixture. Do not provision images or upload source
until the exact build context and live action are prepared for review.
Do not implement the API/dashboard or optional LangSmith tracing.
