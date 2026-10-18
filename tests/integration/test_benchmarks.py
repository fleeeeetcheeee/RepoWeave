import json
import subprocess
import sys
from pathlib import Path

import pytest

from repoweave.agents.schemas import summarize_benchmark
from repoweave.benchmarks.runner import _measure, benchmark
from repoweave.benchmarks.splits import Splits
from repoweave.benchmarks.worker import _tar_decode, _tar_encode
from repoweave.dictionaries.train import train_dictionaries
from repoweave.repository import create_snapshot
from repoweave.routing.schemas import Policy


@pytest.fixture
def snapshot(tmp_path: Path):
    root = tmp_path / "input"
    root.mkdir()
    (root / "empty-dir").mkdir()
    (root / "empty-file").write_bytes(b"")
    (root / "é.py").write_bytes(b"import nothing\n" * 100)
    (root / "é.py").chmod(0o711)
    (root / "binary").write_bytes(bytes(range(256)))
    return create_snapshot(root, tmp_path / "snapshots")


@pytest.mark.parametrize("solid_block_bytes", [None, 65536])
def test_real_benchmark_cli_without_credentials(tmp_path: Path, solid_block_bytes: int | None) -> None:
    root = tmp_path / "input"
    root.mkdir()
    (root / "hello.py").write_bytes(b"print('synthetic')\n" * 100)
    policy = tmp_path / "policy.json"
    policy.write_text(Policy(solid_block_bytes=solid_block_bytes).model_dump_json())
    output = tmp_path / "results.json"
    command = [sys.executable, "-m", "repoweave.cli", "benchmark", str(root), "--policy", str(policy),
               "--output", str(output), "--warmups", "0", "--repeats", "2"]
    result = subprocess.run(command, cwd=tmp_path, env={}, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text())
    assert report["original_bytes"] == 1900
    assert report["partition"] == "unpartitioned-exploratory"
    evidence = summarize_benchmark(report)
    assert evidence.snapshot_id == report["snapshot"]["snapshot_id"]
    assert len(evidence.results) == len(report["results"]) == 4
    assert "hello.py" not in evidence.model_dump_json()
    assert {r["name"] for r in report["results"]} == {"candidate", "rpw-zstd", "tar-zstd", "tar-lzma"}
    for measurement in report["results"]:
        assert measurement["status"] == "ok", measurement
        assert measurement["eligible"]
        assert len(measurement["samples"]) == 2
        for sample in measurement["samples"]:
            assert sample["reconstructed"]
            assert sample["encode_peak_rss_bytes"] > 0
            assert sample["external_dependency_bytes"] == 0
            if measurement["method"] == "rpw":
                assert sample["archive_bytes"] == 18 + sample["manifest_bytes"] + sample["payload_bytes"]
    before = output.read_bytes()
    assert subprocess.run(command, cwd=tmp_path, env={}, capture_output=True, check=False).returncode == 2
    assert output.read_bytes() == before


@pytest.mark.parametrize("codec", ["zstd", "lzma"])
def test_tar_normalized_deterministic_and_exact(snapshot, tmp_path: Path, codec: str) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    _tar_encode(snapshot, first, codec)
    _tar_encode(snapshot, second, codec)
    assert first.read_bytes() == second.read_bytes()
    restored = tmp_path / "restored"
    _tar_decode(snapshot, first, restored, codec)
    from repoweave.benchmarks.worker import _compare

    _compare(snapshot, restored)
    (restored / "binary").write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        _compare(snapshot, restored)


def test_missing_assets_produce_failed_candidate_not_winner(snapshot, tmp_path: Path) -> None:
    report = benchmark(snapshot, Policy(dictionary_id="a" * 64), data_dir=tmp_path / "data",
                       repeats=2, warmups=0)
    candidate = report["results"][0]
    assert candidate["status"] == "failed"
    assert candidate["score_bytes"] is None
    assert report["smallest_eligible"] != "candidate"
    assert report["asset_errors"] == ["candidate"]


def test_timeout_terminates_child(snapshot, tmp_path: Path) -> None:
    result = _measure(snapshot, tmp_path / "archive", tmp_path / "out", "rpw", "encode",
                      Policy(), tmp_path / "registry", timeout=0.000001)
    assert result == {"status": "failed", "error": "timeout"}


def test_training_snapshot_and_test_search_are_rejected(snapshot, tmp_path: Path) -> None:
    splits = Splits(train=["train"], validation=["validate"], test=["heldout"])
    data_dir = tmp_path / "data"
    bundle = train_dictionaries({"train": snapshot}, splits, grouping="none")
    bundle.save(data_dir / "dictionaries")
    with pytest.raises(ValueError, match="Training"):
        benchmark(snapshot, Policy(dictionary_id=bundle.dictionary_id), data_dir=data_dir,
                  splits=splits, repository_id="validate")
    with pytest.raises(ValueError, match="final"):
        benchmark(snapshot, Policy(), data_dir=data_dir, splits=splits,
                  repository_id="heldout", partition="test")


def test_corrupt_snapshot_is_failed_in_child(snapshot, tmp_path: Path) -> None:
    path = snapshot.files_root / "binary"
    path.chmod(0o600)
    path.write_bytes(b"changed")
    result = _measure(snapshot, tmp_path / "archive", tmp_path / "out", "rpw", "encode",
                      Policy(), tmp_path / "registry", timeout=20)
    assert result == {"status": "failed", "error": "ArchiveFormatError"}


def test_corrupt_archive_is_failed_in_decode_child(snapshot, tmp_path: Path) -> None:
    from repoweave.archive.writer import write_archive

    archive = tmp_path / "archive"
    write_archive(snapshot, archive)
    data = archive.read_bytes()
    archive.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    result = _measure(snapshot, archive, tmp_path / "restored", "rpw", "decode",
                      Policy(), tmp_path / "registry", timeout=20)
    assert result == {"status": "failed", "error": "ArchiveFormatError"}
    assert not (tmp_path / "restored").exists()
