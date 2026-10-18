Read AGENTS.md and DESIGN.md step 8.4 before editing.
Add deterministic synthetic large-repository tests under tests/integration using pytest
tmp_path and credential-free local CLI subprocesses, matching existing tests.
Cover thousands of small files, a deep cyclic import graph, and 128 MiB of input
including a 64 MiB file and incompressible bytes. Exercise real analysis and the existing
fresh-process RPW/tar benchmarks, exact reconstruction, archive overhead, repeatability,
time/RSS limits, and benchmark cleanup. Include these checks in the normal test suite.
Run locked pytest, Ruff, and mypy; document the actual scope and remaining limits.
Do not change dictionary selection, add dependencies/services, download repositories,
read private datasets, execute fixture source, or begin step 9.
