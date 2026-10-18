Read AGENTS.md and DESIGN.md steps 10–12. Complete the existing optimization
scaffolding and optional LangSmith integration. Use references-only explicit spans,
disable automatic state/prompt capture, and prove sentinel redaction and offline use.
Complete bounded Daytona execution, verified artifact export, a separate clean decoder,
recovery/cleanup, and CLI operations. Use the existing engine snapshot without changing
its revision. Test with fake agents and Daytona boundaries and real engine round trips.
The requested live validation uses a generated owned synthetic fixture, sanitized
Factory measurements and LangSmith references, and at most two sequential sandboxes
(2 CPU, 4 GiB RAM, 10 GiB disk, ten minutes each). Preserve receipts locally and delete
run-owned sandboxes. Do not upload the checkout, credentials, or private datasets.
Do not implement API/dashboard/neural codecs or automatically promote live candidates.
