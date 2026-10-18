"""Extract a bounded Python import graph without importing repository modules."""

import ast
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath

from repoweave.archive.filesystem import open_regular
from repoweave.archive.integrity import check_bytes
from repoweave.repository import Snapshot

MAX_SOURCE_BYTES = 1024 * 1024


@dataclass(frozen=True, order=True)
class Edge:
    source: str
    target: str
    kind: str


@dataclass(frozen=True, order=True)
class Diagnostic:
    path: str
    kind: str
    module: str = ""
    line: int = 0


@dataclass(frozen=True)
class RepositoryGraph:
    nodes: tuple[str, ...]
    edges: tuple[Edge, ...]
    diagnostics: tuple[Diagnostic, ...]

    def to_report(self) -> dict[str, object]:
        return {
            "schema_version": 1, "kind": "repository-imports",
            "nodes": list(self.nodes), "edges": [asdict(edge) for edge in self.edges],
            "diagnostics": [asdict(item) for item in self.diagnostics],
        }


def build_graph(snapshot: Snapshot) -> RepositoryGraph:
    nodes = tuple(entry.path for entry in snapshot.entries if entry.kind == "file")
    modules: dict[str, set[str]] = {}
    names: dict[str, str] = {}
    for path in nodes:
        if not path.endswith(".py"):
            continue
        parts = list(PurePosixPath(path).with_suffix("").parts)
        if parts[0] == "src" and len(parts) > 1:
            parts.pop(0)
        if parts[-1] == "__init__":
            parts.pop()
        name = ".".join(parts)
        modules.setdefault(name, set()).add(path)
        names[path] = name
    edges: set[Edge] = set()
    diagnostics: set[Diagnostic] = set()
    for entry in snapshot.entries:
        path = entry.path
        if path not in names:
            continue
        if (entry.size_bytes or 0) > MAX_SOURCE_BYTES:
            diagnostics.add(Diagnostic(path, "source-size-limit"))
            continue
        with open_regular(snapshot.files_root / path) as stream:
            data = stream.read(MAX_SOURCE_BYTES + 1)
        assert entry.size_bytes is not None and entry.sha256 is not None
        check_bytes(data, entry.size_bytes, entry.sha256)
        try:
            tree = ast.parse(data, filename="<snapshot>")
        except (SyntaxError, ValueError, RecursionError):
            diagnostics.add(Diagnostic(path, "syntax-error"))
            continue
        package = names[path].split(".")
        if not path.endswith("/__init__.py") and path != "__init__.py":
            package = package[:-1]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                requests = [(alias.name, "import") for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > len(package):
                    diagnostics.add(Diagnostic(path, "unresolved-relative", node.module or "",
                                               node.lineno))
                    continue
                prefix = package[:len(package) - node.level + 1] if node.level else []
                base = ".".join([*prefix, *([node.module] if node.module else [])])
                requests = []
                for alias in node.names:
                    child = f"{base}.{alias.name}" if base else alias.name
                    requests.append((child if child in modules else base, "from-import"))
            else:
                continue
            for module, kind in requests:
                targets = modules.get(module, set())
                if len(targets) == 1:
                    edges.add(Edge(path, next(iter(targets)), kind))
                else:
                    diagnostics.add(Diagnostic(
                        path, "ambiguous-import" if targets else "unresolved-import",
                        module, node.lineno,
                    ))
    return RepositoryGraph(tuple(sorted(nodes)), tuple(sorted(edges)), tuple(sorted(diagnostics)))
