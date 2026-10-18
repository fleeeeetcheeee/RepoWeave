# DESIGN.md step 14 — local dashboard and demonstration

Implement the React/Vite/TypeScript Repository, Archive, Benchmarks and Optimization
views against the real persistent API. Add only the read contracts and independent
verification receipts needed by those views. Recover state and replay SSE after
refresh; keep failures and no-improvement results honest. Generate request/response
types from OpenAPI. Keep cloud credentials on the backend and public deployment
optional. Do not modify the archive format or deterministic engine.

Add a fresh-directory local demo launcher, an automated browser end-to-end test
using real compression/persistence with mocked Factory/Daytona boundaries, and
run a separately labeled live smoke demo with the owned fixture. Verify downloads,
eligible promotion, old-archive decoding, restart/reconnect and sandbox cleanup.
Run relevant API, browser, build and static checks. Record exactly which live gates
passed. Preserve the existing uncommitted hosting setup from the prior task.
