"""Bounded local scale checks; all repository contents are invented test data."""

import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

from repoweave.archive.manifest import HEADER
from repoweave.routing.schemas import Policy


@pytest.fixture(params=["many-files", "large-files"])
def large_repository(tmp_path: Path, request) -> tuple[Path, int, int]:
    root = tmp_path / "input"
    root.mkdir()
    (root / "empty-dir").mkdir()
    (root / "empty-file").write_bytes(b"")
    (root / "日本語.md").write_bytes(b"synthetic fixture\r\n")
    if request.param == "many-files":
        # The import cycle exceeds Python's usual recursion limit; never execute these files.
        for index in range(2048):
            source = (
                f"import module_{(index + 1) % 2048:04d}\n"
                "raise RuntimeError('fixture must never execute')\n"
            ).encode() + b"# synthetic source padding\n" * 40
            (root / f"module_{index:04d}.py").write_bytes(source)
        (root / "module_0000.py").chmod(0o751)
        assets = root / "assets"
        assets.mkdir()
        for index in range(64):
            (assets / f"{index:04d}.json").write_text(
                json.dumps({"index": index, "values": list(range(256))}), encoding="utf-8",
            )
        count = 2114
    else:
        block = bytes(range(256)) * 4096
        for name, mebibytes in (("at-file-limit.bin", 64), ("repeated.bin", 56)):
            with (root / name).open("xb") as output:
                for _ in range(mebibytes):
                    output.write(block)
        generator = random.Random(78)
        with (root / "incompressible.bin").open("xb") as output:
            for _ in range(8):
                output.write(generator.randbytes(1024 * 1024))
        count = 5
    total = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    return root, count, total


def test_large_repository_analysis_and_benchmark(
    large_repository: tuple[Path, int, int], tmp_path: Path,
) -> None:
    root, count, total = large_repository
    analysis_path = tmp_path / "analysis.json"
    analyzed = subprocess.run(
        [sys.executable, "-m", "repoweave.cli", "analyze", str(root),
         "--output", str(analysis_path)],
        cwd=tmp_path, env={}, capture_output=True, text=True, timeout=120, check=False,
    )
    assert analyzed.returncode == 0, analyzed.stderr
    analysis = json.loads(analysis_path.read_text())
    assert analysis["file_count"] == count
    assert analysis["total_bytes"] == total
    if count == 2114:
        graph = analysis["repository_graph"]
        assert len(graph["nodes"]) == count
        assert len(graph["edges"]) == 2048
        assert not graph["diagnostics"]
        context = analysis["candidate_context"]
        positions = {path: index for index, path in enumerate(context["order"])}
        assert len(positions) == count
        assert len(context["removed_cycle_edges"]) == 1
        assert len(context["edges"]) == 2047
        assert all(positions[edge["target"]] < positions[edge["source"]]
                   for edge in context["edges"])
    else:
        assert total == 128 * 1024 * 1024 + len(b"synthetic fixture\r\n")

    policy = tmp_path / "policy.json"
    policy.write_text(Policy().model_dump_json())
    report_path = tmp_path / "benchmark.json"
    completed = subprocess.run(
        [sys.executable, "-m", "repoweave.cli", "benchmark", str(root),
         "--policy", str(policy), "--output", str(report_path),
         "--warmups", "1", "--repeats", "2", "--max-encode-seconds", "300",
         "--max-decode-seconds", "300", "--max-memory-bytes", str(1024 * 1024 * 1024)],
        cwd=tmp_path, env={}, capture_output=True, text=True, timeout=600, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text())
    assert report["snapshot"]["snapshot_id"] == analysis["snapshot_id"]
    assert report["original_bytes"] == total
    assert report["warmups"] == 1
    assert {result["name"] for result in report["results"]} == {
        "candidate", "rpw-zstd", "tar-zstd", "tar-lzma",
    }
    for result in report["results"]:
        assert result["status"] == "ok", result
        assert result["eligible"], result
        assert len(result["samples"]) == 2
        assert len({sample["archive_bytes"] for sample in result["samples"]}) == 1
        for sample in result["samples"]:
            assert sample["reconstructed"]
            assert sample["external_dependency_bytes"] == 0
            assert 0 < sample["encode_peak_rss_bytes"] <= 1024 * 1024 * 1024
            assert 0 < sample["decode_peak_rss_bytes"] <= 1024 * 1024 * 1024
            assert 0 < sample["encode_seconds"] <= 300
            assert 0 < sample["decode_seconds"] <= 300
            if result["method"] == "rpw":
                assert sample["archive_bytes"] == (
                    HEADER.size + sample["manifest_bytes"] + sample["payload_bytes"]
                )
    assert not list((tmp_path / ".repoweave").glob("benchmark-*"))
