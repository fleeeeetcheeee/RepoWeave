"""Deterministic experiment groups and candidate context, never decoder requirements."""

from dataclasses import asdict
from pathlib import PurePosixPath

from .build import RepositoryGraph


def group_files(graph: RepositoryGraph, strategy: str) -> dict[str, tuple[str, ...]]:
    groups: dict[str, list[str]] = {}
    if strategy in ("none", "extension"):
        for path in graph.nodes:
            key = "all" if strategy == "none" else PurePosixPath(path).suffix.lower() or "<none>"
            groups.setdefault(key, []).append(path)
    elif strategy == "graph":
        adjacency: dict[str, set[str]] = {path: set() for path in graph.nodes}
        for edge in graph.edges:
            adjacency[edge.source].add(edge.target)
            adjacency[edge.target].add(edge.source)
        remaining = set(graph.nodes)
        for start in graph.nodes:
            if start not in remaining:
                continue
            pending = [start]
            component = []
            remaining.remove(start)
            while pending:
                path = pending.pop()
                component.append(path)
                for neighbor in sorted(adjacency[path]):
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        pending.append(neighbor)
            groups[start] = component
    else:
        raise ValueError("Unsupported grouping strategy")
    return {key: tuple(sorted(paths)) for key, paths in sorted(groups.items())}


def context_order(graph: RepositoryGraph) -> dict[str, object]:
    adjacency: dict[str, set[str]] = {path: set() for path in graph.nodes}
    for edge in graph.edges:
        adjacency[edge.source].add(edge.target)
    visited: set[str] = set()
    order: list[str] = []
    for start in graph.nodes:
        pending = [(start, False)]
        while pending:
            path, expanded = pending.pop()
            if expanded:
                order.append(path)
            elif path not in visited:
                visited.add(path)
                pending.append((path, True))
                pending.extend((target, False) for target in sorted(adjacency[path], reverse=True))
    positions = {path: index for index, path in enumerate(order)}
    kept: list[dict] = []
    removed: list[dict] = []
    for edge in graph.edges:
        target = kept if positions[edge.target] < positions[edge.source] else removed
        target.append(asdict(edge))
    return {"kind": "candidate-context", "order": order, "edges": kept,
            "removed_cycle_edges": removed, "actual_decode_dependencies": []}
