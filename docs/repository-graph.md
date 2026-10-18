# Static repository analysis

`repoweave analyze INPUT --output REPORT.json` now adds `repository_graph`,
`groups`, and `candidate_context` to the existing snapshot report. These contain
metadata only. Analysis reads the captured bytes, checks their hashes, and uses
Python's `ast.parse`; it never imports or executes repository code.

Every supported regular file is a node identified by its snapshot-relative path.
Edges point from the importing file to the imported file, with kind `import` or
`from-import`. Edges and diagnostics are deduplicated and sorted. Ordinary absolute
imports and package-relative imports resolve against Python files at the repository
root or under `src/`. `__init__.py` represents its package. `from package import name`
prefers a matching child module, otherwise the package (the name may be an attribute).
Namespace package containers without a file have no node. Ambiguous module names,
external/unresolved imports, and relative imports escaping the package are diagnostics.
Imports inside functions, conditionals, and type-checking blocks are static candidates.

Invalid syntax/encoding produces `syntax-error` without storing exception text or
source snippets. Python files larger than 1 MiB produce `source-size-limit`. Files
in other languages remain nodes with no extracted edges. Dynamic imports, import
hooks, sys.path changes, and full Python runtime import semantics are outside this step.

`group_files(graph, strategy)` returns deterministic partitions of all file nodes:

- `none`: one `all` group.
- `extension`: lowercase suffix, with `<none>` for extensionless files.
- `graph`: weakly connected import components, keyed by the smallest relative path.
  Isolated files are individual groups. Direction is preserved in the graph itself.

Graph group keys match across repositories only when their representative relative
paths match. Missing dictionary groups explicitly fall back to ordinary zstd. This
simple grouping is a hypothesis to measure against the extension control.

`candidate_context` uses deterministic dependency-first DFS. It retains only edges
whose targets occur earlier in the chosen order, and records removed cycle/self edges.
It is labeled separately from `repository-imports`; `actual_decode_dependencies` is
empty. Current archives use independent file frames and embedded dictionary assets,
not source-file context. The UI can consume these distinct labels later in step 14.
