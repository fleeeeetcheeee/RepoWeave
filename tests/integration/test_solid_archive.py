import hashlib
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from repoweave.archive.decode import unpack
from repoweave.archive.encode import pack
from repoweave.archive.manifest import HEADER, MAGIC, MAX_FILE_BYTES, ArchiveFormatError
from repoweave.archive.reader import check_archive
from repoweave.archive.solid import build_solid
from repoweave.archive.writer import _snapshot_manifest, write_archive
from repoweave.benchmarks.worker import _compare
from repoweave.repository import create_snapshot
from repoweave.routing.policy import content_id
from repoweave.routing.schemas import Policy, load_policy


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "input"
    (root / "nested" / "empty").mkdir(parents=True)
    shared = random.Random(71).randbytes(4096)
    for index in range(24):
        (root / f"nested/{index:03}.bin").write_bytes(shared + bytes([index]))
    (root / "nested/000.bin").chmod(0o711)
    (root / "empty").write_bytes(b"")
    (root / "日本語").write_bytes(b"")
    return root


def test_solid_policy_roundtrip_smaller_and_deterministic(source: Path, tmp_path: Path) -> None:
    policy = Policy(solid_block_bytes=16388)
    first = tmp_path / "solid.rpw"
    snapshot = pack(source, first, data_dir=tmp_path / "state", policy=policy)
    second = tmp_path / "second.rpw"
    pack(source, second, data_dir=tmp_path / "state", policy=policy)
    assert first.read_bytes() == second.read_bytes()
    plain = tmp_path / "plain.rpw"
    write_archive(snapshot, plain, parameters=policy.parameters)
    assert first.stat().st_size < plain.stat().st_size
    assert HEADER.unpack(first.read_bytes()[:HEADER.size])[1] == 3
    manifest = unpack(first, tmp_path / "restored")
    _compare(snapshot, tmp_path / "restored")
    assert len(manifest.blocks) > 1
    assert max(block.size for block in manifest.blocks) <= 16388
    assert manifest.payload_size == first.stat().st_size - HEADER.size - HEADER.unpack(
        first.read_bytes()[:HEADER.size])[2]
    with first.open("rb") as stream:
        assert check_archive(stream) == manifest
    assert not list(tmp_path.glob(".repoweave-*"))


def test_solid_clean_decoder_and_cli_policy(source: Path, tmp_path: Path) -> None:
    policy = Policy(solid_block_bytes=65536)
    policy_path = policy.save(tmp_path / "policies")
    archive = tmp_path / "solid.rpw"
    packed = subprocess.run([
        sys.executable, "-m", "repoweave.cli", "pack", str(source), "-o", str(archive),
        "--policy", str(policy_path),
    ], cwd=tmp_path, env={}, capture_output=True, check=False)
    assert packed.returncode == 0, packed.stderr
    assert HEADER.unpack(archive.read_bytes()[:HEADER.size])[1] == 3
    result = subprocess.run([sys.executable, "-c", """
import sys
from pathlib import Path
from repoweave.archive.decode import unpack
unpack(Path(sys.argv[1]), Path(sys.argv[2]))
assert not any(name.startswith(('repoweave.config', 'repoweave.agents',
    'repoweave.workflows', 'daytona', 'langsmith', 'langgraph', 'dotenv')) for name in sys.modules)
""", str(archive), str(tmp_path / "restored")], cwd=tmp_path, env={}, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    for path in source.rglob("*"):
        restored = tmp_path / "restored" / path.relative_to(source)
        assert restored.is_dir() if path.is_dir() else restored.read_bytes() == path.read_bytes()


def test_solid_fresh_process_benchmark_accounting(source: Path, tmp_path: Path) -> None:
    from repoweave.benchmarks.runner import benchmark

    snapshot = create_snapshot(source, tmp_path / "snapshots")
    report = benchmark(snapshot, Policy(solid_block_bytes=65536), data_dir=tmp_path / "state",
                       repeats=2, warmups=0)
    candidate, baseline = report["results"][:2]
    assert candidate["eligible"] and baseline["eligible"]
    assert candidate["score_bytes"] < baseline["score_bytes"]
    for sample in candidate["samples"]:
        assert sample["reconstructed"]
        assert sample["archive_bytes"] == HEADER.size + sample["manifest_bytes"] + sample["payload_bytes"]
        assert sample["embedded_dictionary_bytes"] == sample["external_dependency_bytes"] == 0


@pytest.mark.parametrize("case", ["empty-tree", "empty-files", "random", "tiny"])
def test_unhelpful_solid_falls_back_byte_for_byte(tmp_path: Path, case: str) -> None:
    source = tmp_path / "input"
    source.mkdir()
    if case == "empty-files":
        (source / "a").write_bytes(b"")
        (source / "b").write_bytes(b"")
    elif case != "empty-tree":
        (source / "a").write_bytes(random.Random(12).randbytes(16384) if case == "random" else b"x")
    snapshot = create_snapshot(source, tmp_path / "snapshots")
    for name, target in (("plain", None), ("solid", 65536)):
        write_archive(snapshot, tmp_path / name, parameters={"level": 3}, solid_block_bytes=target)
    assert (tmp_path / "plain").read_bytes() == (tmp_path / "solid").read_bytes()


def test_exact_target_oversized_singleton_and_stored_blocks(tmp_path: Path) -> None:
    root = tmp_path / "input"
    root.mkdir()
    contents = [b"", b"a" * 6, b"b" * 4, random.Random(7).randbytes(25), b"c", b""]
    for index, data in enumerate(contents):
        (root / str(index)).write_bytes(data)
    snapshot = create_snapshot(root, tmp_path / "snapshots")
    with tempfile.TemporaryFile() as spool:
        manifest, locations = build_solid(snapshot, _snapshot_manifest(snapshot), {}, 10, spool)
        assert [block.size for block in manifest.blocks] == [10, 25, 1]
        assert len(locations) == 3
        assert all(block.codec == "stored" for block in manifest.blocks)
    from repoweave.archive.manifest import encode_manifest

    # Exercise V3 stored decoding even when the full-archive writer would prefer V1.
    encoded = encode_manifest(manifest)
    archive = tmp_path / "stored-blocks.rpw"
    archive.write_bytes(HEADER.pack(MAGIC, 3, len(encoded)) + encoded + b"".join(contents))
    unpack(archive, tmp_path / "restored")
    _compare(snapshot, tmp_path / "restored")


@pytest.mark.parametrize("change", [
    "missing-block", "unused-block", "reorder", "slice-gap", "slice-overlap", "block-gap",
    "block-overlap", "oversize", "uncovered-bytes", "legacy-file", "dictionary", "parameters",
    "v1", "v2", "no-blocks", "unsafe-path", "duplicate-path", "hash", "payload", "truncated",
    "trailing", "bomb", "concatenated-frame", "boolean-index", "out-of-range", "negative-index",
])
def test_malformed_solid_never_publishes(source: Path, tmp_path: Path, change: str) -> None:
    archive = tmp_path / "valid.rpw"
    pack(source, archive, data_dir=tmp_path / "state", policy=Policy(solid_block_bytes=16388))
    raw = archive.read_bytes()
    _, version, length = HEADER.unpack(raw[:HEADER.size])
    document = json.loads(raw[HEADER.size:HEADER.size + length])
    payload = raw[HEADER.size + length:]
    files = [entry for entry in document["entries"] if entry["kind"] == "file"]
    blocks = document["blocks"]
    first = next(entry for entry in files if entry["size"])
    if change == "missing-block":
        blocks.pop()
    elif change == "unused-block":
        blocks.append({**blocks[-1], "offset": len(payload), "size": 0, "length": 0, "codec": "stored",
                       "parameters": {}})
    elif change == "reorder":
        first["block_index"] = 1
    elif change == "slice-gap":
        first["offset"] += 1
    elif change == "slice-overlap":
        files[2]["offset"] = 0
    elif change == "block-gap":
        blocks[0]["offset"] = 1
    elif change == "block-overlap":
        blocks[1]["offset"] = 0
    elif change == "oversize":
        blocks[0]["size"] = MAX_FILE_BYTES + 1
    elif change == "uncovered-bytes":
        blocks[0]["size"] += 1
    elif change == "legacy-file":
        first["codec"] = "stored"
    elif change == "dictionary":
        first["dictionary_id"] = "0" * 64
    elif change == "parameters":
        first["parameters"] = {"level": 3}
    elif change in ("v1", "v2"):
        version = int(change[1])
    elif change == "no-blocks":
        document.pop("blocks")
    elif change == "unsafe-path":
        first["path"] = "../outside"
    elif change == "duplicate-path":
        first["path"] = files[0]["path"]
    elif change == "hash":
        first["sha256"] = "0" * 64
    elif change == "payload":
        payload = bytes([payload[0] ^ 1]) + payload[1:]
    elif change == "truncated":
        payload = payload[:-1]
    elif change == "trailing":
        payload += b"x"
    elif change == "bomb":
        # Valid slice layout with output exceeding the declared block/file bounds.
        for entry in files:
            entry.update(size=0, length=0, offset=0, sha256=hashlib.sha256(b"").hexdigest())
        for block in blocks:
            block["size"] = 0
    elif change == "concatenated-frame":
        extra = payload[:blocks[0]["length"]]
        payload = extra + payload
        blocks[0]["length"] += len(extra)
        for block in blocks[1:]:
            block["offset"] += len(extra)
    else:
        first["block_index"] = {"boolean-index": True, "out-of-range": len(blocks),
                                "negative-index": -1}[change]
    encoded = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    archive.write_bytes(HEADER.pack(MAGIC, version, len(encoded)) + encoded + payload)
    with pytest.raises(ArchiveFormatError):
        unpack(archive, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
    assert not (tmp_path / "outside").exists()
    assert not list(tmp_path.glob(".repoweave-*"))


def test_existing_policy_identity_and_nested_serialization_are_unchanged(tmp_path: Path) -> None:
    from repoweave.api.schemas import Submission

    baseline = json.loads((Path(__file__).resolve().parents[2] / "policies/baseline.json").read_text())
    assert Policy().model_dump() == baseline
    assert Policy().policy_id == content_id(baseline)
    submission = Submission(repository_id="a" * 64, operation="pack")
    assert submission.model_dump()["policy"] == baseline
    solid = Policy(solid_block_bytes=65536)
    assert solid.policy_id != Policy().policy_id
    assert load_policy(solid.save(tmp_path)) == solid
    submitted = Submission(repository_id="a" * 64, operation="pack", policy=solid)
    assert Submission.model_validate_json(submitted.model_dump_json()).policy == solid


@pytest.mark.parametrize("changes", [
    {"solid_block_bytes": 0}, {"solid_block_bytes": True}, {"solid_block_bytes": MAX_FILE_BYTES + 1},
    {"solid_block_bytes": 16, "dictionary_id": "a" * 64},
    {"solid_block_bytes": 16, "codec": "stored"},
    {"solid_block_bytes": 16, "grouping": "graph"},
])
def test_invalid_solid_policy(changes: dict) -> None:
    with pytest.raises(ValidationError):
        Policy.model_validate(changes)
