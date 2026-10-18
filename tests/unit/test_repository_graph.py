from pathlib import Path

from repoweave.repository import create_snapshot
from repoweave.repository_graph import build_graph, context_order, group_files
from repoweave.repository_graph.build import Edge


def capture(tmp_path: Path, files: dict[str, bytes]):
    root = tmp_path / "input"
    root.mkdir()
    for path, data in files.items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    return create_snapshot(root, tmp_path / "captures")


def test_imports_relative_cycles_diagnostics_and_stability(tmp_path: Path) -> None:
    snapshot = capture(tmp_path, {
        "pkg/__init__.py": b"from . import a\n",
        "pkg/a.py": b"from .b import function\nfrom ..outside import item\nimport os\n",
        "pkg/b.py": b"import pkg.a\nfrom pkg import a\n",
        "broken.py": b"def invalid(\n",
        "encoding.py": b"# coding: latin-1\nlabel = '\xe9'\n",
        "README.md": b"ordinary bytes",
    })
    graph = build_graph(snapshot)
    assert graph == build_graph(snapshot)
    assert Edge("pkg/__init__.py", "pkg/a.py", "from-import") in graph.edges
    assert Edge("pkg/a.py", "pkg/b.py", "from-import") in graph.edges
    assert Edge("pkg/b.py", "pkg/a.py", "import") in graph.edges
    assert {(d.path, d.kind) for d in graph.diagnostics} == {
        ("pkg/a.py", "unresolved-import"), ("pkg/a.py", "unresolved-relative"),
        ("broken.py", "syntax-error"),
    }
    groups = group_files(graph, "graph")
    assert groups["pkg/__init__.py"] == ("pkg/__init__.py", "pkg/a.py", "pkg/b.py")
    assert group_files(graph, "extension")[".md"] == ("README.md",)
    context = context_order(graph)
    assert context["kind"] == "candidate-context"
    assert context["actual_decode_dependencies"] == []
    assert context["removed_cycle_edges"]
    order = context["order"]
    assert len(set(order)) == len(graph.nodes)
    for edge in context["edges"]:
        assert order.index(edge["target"]) < order.index(edge["source"])


def test_source_is_never_executed_and_snapshot_is_used(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    snapshot = capture(tmp_path, {"a.py": f"open({str(marker)!r}, 'w').write('bad')\nimport b".encode(),
                                  "b.py": b"raise RuntimeError('do not execute')"})
    (tmp_path / "input/a.py").write_bytes(b"invalid syntax!")
    assert build_graph(snapshot).edges == (Edge("a.py", "b.py", "import"),)
    assert not marker.exists()


def test_src_layout_ambiguous_modules_and_size_limit(tmp_path: Path) -> None:
    snapshot = capture(tmp_path, {"src/pkg/__init__.py": b"", "src/pkg/a.py": b"from . import b",
                                  "src/pkg/b.py": b"", "b.py": b"", "src/b.py": b"",
                                  "main.py": b"import b", "huge.py": b"#" * (1024 * 1024 + 1)})
    graph = build_graph(snapshot)
    assert Edge("src/pkg/a.py", "src/pkg/b.py", "from-import") in graph.edges
    assert {(d.path, d.kind) for d in graph.diagnostics} == {
        ("main.py", "ambiguous-import"), ("huge.py", "source-size-limit"),
    }
