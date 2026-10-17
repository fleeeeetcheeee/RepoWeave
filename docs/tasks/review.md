Read AGENTS.md and DESIGN.md. This is a review only: do not edit, create, or delete files.
Do not execute source files from the repository. Read-only git commands (git status, git diff,
git diff --staged, git log) are allowed. Do not read or print .env or any credentials.

Review the uncommitted changes (staged and unstaged) against main. Identify the DESIGN.md step
the change implements, and check it against that step's "Done when" criteria.

Check for:
- Scope: changes beyond the single requested step; callers and tests not updated.
- Lossless: anything that could break byte-for-byte reconstruction of supported files.
- Archive safety: unsafe paths (absolute, "..", symlinks, collisions), unsupported versions,
  missing assets, unbounded sizes or decompression output, non-atomic writes.
- Layering: archive, codecs, and repository modules importing agent, cloud, or tracing code.
- Secrets: credentials, private data, or file bodies in code, logs, errors, tests, or commits.
- Dependencies: changes to pyproject.toml or uv.lock without a task-related reason.
- Tests: missing failure-path coverage, tests that cannot fail, live tests not marked `live`.

Report findings ordered by severity (blocking, should-fix, minor). For each give the file and
line, the problem, and a concrete failure scenario or fix. If there are no findings, say so.
End with the commands you ran, what you could not verify, and what remains untested.
