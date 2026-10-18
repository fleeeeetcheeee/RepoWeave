import json
import random
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from repoweave.archive.decode import unpack
from repoweave.archive.manifest import HEADER, MAGIC, MAX_MANIFEST_BYTES, ArchiveFormatError
from repoweave.archive.metadata import (
    PREFIX,
    decode_compact,
    decode_metadata,
    encode_compact,
    encode_metadata,
)
from repoweave.archive.reader import read_manifest
from repoweave.benchmarks.worker import _compare
from repoweave.repository import create_snapshot
from repoweave.routing.encode import encode_policy
from repoweave.routing.schemas import Policy


@pytest.fixture
def snapshot(tmp_path):
    root = tmp_path / "input"
    (root / "nested" / "empty").mkdir(parents=True)
    shared = random.Random(31).randbytes(2048)
    for index in range(30):
        (root / "nested" / f"é{index:03}.py").write_bytes(shared + bytes([index]))
    (root / "nested/é000.py").chmod(0o711)
    (root / "empty").write_bytes(b"")
    return create_snapshot(root, tmp_path / "snapshots")


@pytest.mark.parametrize("codec", ["zstd", "lzma"])
@pytest.mark.parametrize("target", [None, 8196])
@pytest.mark.parametrize("metadata", ["compressed", "compact"])
def test_compressed_metadata_exact_deterministic_and_smaller(snapshot, tmp_path, codec, target, metadata):
    plain = tmp_path / "plain.rpw"
    encoded = tmp_path / "encoded.rpw"
    original = Policy(codec=codec, solid_block_bytes=target)
    policy = Policy(codec=codec, solid_block_bytes=target, metadata_encoding=metadata)
    encode_policy(snapshot, plain, original, tmp_path / "registry")
    expected = encode_policy(snapshot, encoded, policy, tmp_path / "registry")
    encode_policy(snapshot, tmp_path / "again.rpw", policy, tmp_path / "registry")
    assert encoded.read_bytes() == (tmp_path / "again.rpw").read_bytes()
    assert encoded.stat().st_size < plain.stat().st_size
    assert HEADER.unpack(encoded.read_bytes()[:HEADER.size])[1] == 4
    assert unpack(encoded, tmp_path / "restored") == expected
    _compare(snapshot, tmp_path / "restored")
    with encoded.open("rb") as stream:
        assert read_manifest(stream) == expected
        assert stream.tell() + expected.payload_size == encoded.stat().st_size
    if codec == "lzma" and target:
        assert any(block.codec == "lzma" for block in expected.blocks)


@pytest.mark.parametrize("change", ["kind", "codec", "zero-size", "excessive-size", "wrong-size",
                                     "truncated", "trailing", "double-frame", "header-limit"])
def test_metadata_corruption_never_publishes(snapshot, tmp_path, change):
    archive = tmp_path / "archive.rpw"
    encode_policy(snapshot, archive, Policy(metadata_encoding="compressed"), tmp_path / "registry")
    data = archive.read_bytes()
    _, version, length = HEADER.unpack(data[:HEADER.size])
    wire, payload = data[HEADER.size:HEADER.size + length], data[HEADER.size + length:]
    kind, codec, size = PREFIX.unpack(wire[:PREFIX.size])
    body = wire[PREFIX.size:]
    if change == "kind":
        kind = 255
    elif change == "codec":
        codec = 255
    elif change == "zero-size":
        size = 0
    elif change == "excessive-size":
        size = MAX_MANIFEST_BYTES + 1
    elif change == "wrong-size":
        size = 1
    elif change == "truncated":
        body = body[:-1]
    elif change == "trailing":
        body += b"extra"
    elif change == "double-frame":
        body += body
    wire = PREFIX.pack(kind, codec, size) + body
    archive.write_bytes(HEADER.pack(MAGIC, version,
                                   MAX_MANIFEST_BYTES + 1 if change == "header-limit" else len(wire))
                        + wire + payload)
    with pytest.raises(ArchiveFormatError):
        unpack(archive, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
    assert not list(tmp_path.glob(".repoweave-*"))


def test_v3_rejects_lzma_blocks(snapshot, tmp_path):
    from repoweave.archive.manifest import encode_manifest, parse_manifest

    manifest = encode_policy(snapshot, tmp_path / "lzma.rpw", Policy(codec="lzma", solid_block_bytes=8196),
                             tmp_path / "registry")
    assert manifest.blocks
    with pytest.raises(ArchiveFormatError):
        parse_manifest(encode_manifest(manifest), payload_size=manifest.payload_size, version=3)


def test_defaults_preserve_policy_serialization_and_small_archive_fallback(tmp_path):
    from repoweave.archive.manifest import Manifest
    from repoweave.routing.policy import content_id

    baseline = json.loads((Path(__file__).resolve().parents[2] / "policies/baseline.json").read_text())
    assert Policy(metadata_encoding="json").model_dump() == baseline
    assert Policy().policy_id == content_id(baseline)
    assert encode_metadata(Manifest(entries=[]), "compressed") == (1, b'{"entries":[]}')


@pytest.mark.parametrize("metadata", ["compressed", "compact"])
def test_v4_clean_decoder_process(snapshot, tmp_path, metadata):
    archive = tmp_path / "archive.rpw"
    encode_policy(snapshot, archive, Policy(codec="lzma", solid_block_bytes=8196,
                                           metadata_encoding=metadata), tmp_path / "registry")
    result = subprocess.run([sys.executable, "-c", """
import sys
from pathlib import Path
from repoweave.archive.decode import unpack
unpack(Path(sys.argv[1]), Path(sys.argv[2]))
assert not any(name.startswith(('repoweave.config', 'repoweave.agents', 'repoweave.workflows',
    'daytona', 'langsmith', 'langgraph', 'dotenv')) for name in sys.modules)
""", str(archive), str(tmp_path / "restored")], cwd=tmp_path, env={}, check=False, capture_output=True)
    assert result.returncode == 0, result.stderr
    _compare(snapshot, tmp_path / "restored")


def test_v4_json_is_still_canonical_and_payload_size_checked():
    for raw in (b'{ "entries": [] }', b'{"entries":[],"entries":[]}'):
        with pytest.raises(ArchiveFormatError):
            decode_metadata(PREFIX.pack(0, 0, len(raw)) + raw, payload_size=0)
    with pytest.raises(ArchiveFormatError):
        decode_metadata(PREFIX.pack(0, 0, 14) + b'{"entries":[]}', payload_size=1)


def test_compact_known_bytes_and_maximal_prefix():
    from repoweave.archive.manifest import Manifest

    manifest = Manifest.model_validate({"entries": [
        {"kind": "directory", "path": "abc"}, {"kind": "directory", "path": "abd"},
    ]})
    prefix = struct.pack(">IIHH", 2, 0, 0, 3) + b"abc\0"
    expected = prefix + struct.pack(">HH", 2, 1) + b"d\0"
    assert encode_compact(manifest) == expected
    assert decode_compact(expected, payload_size=0) == manifest
    with pytest.raises(ArchiveFormatError):
        decode_compact(prefix + struct.pack(">HH", 0, 3) + b"abd\0", payload_size=0)


def test_compact_prefix_can_end_inside_utf8_character():
    from repoweave.archive.manifest import Manifest

    manifest = Manifest.model_validate({"entries": [
        {"kind": "directory", "path": "é"}, {"kind": "directory", "path": "ê"},
    ]})
    raw = encode_compact(manifest)
    assert decode_compact(raw, payload_size=0) == manifest
    # Two-byte code points share their first byte; the suffix itself is not valid UTF-8.
    assert raw.endswith(struct.pack(">HH", 1, 1) + b"\xaa\0")


@pytest.mark.parametrize("payload_size", [True, False, -1, 2 ** 31 + 1, 0.0])
def test_v4_rejects_invalid_payload_size_types_and_bounds(payload_size):
    with pytest.raises(ArchiveFormatError):
        decode_metadata(PREFIX.pack(0, 0, 14) + b'{"entries":[]}', payload_size=payload_size)
    with pytest.raises(ArchiveFormatError):
        decode_compact(struct.pack(">II", 0, 0), payload_size=payload_size)


@pytest.mark.parametrize("raw", [
    b"", b"\0" * 7, struct.pack(">II", 100001, 0), struct.pack(">II", 0, 100001),
    struct.pack(">II", 0, 0) + b"extra", struct.pack(">IIHH", 1, 0, 1, 1) + b"a\0",
    struct.pack(">IIHH", 1, 0, 0, 1025), struct.pack(">IIHH", 1, 0, 0, 1) + b"\xff\0",
    struct.pack(">IIHH", 1, 0, 0, 1) + b"a\xff",
    struct.pack(">IIHH", 1, 0, 0, 4) + b"../a\0",
    struct.pack(">IIBbIII", 0, 1, 255, -128, 0, 0, 0),
    struct.pack(">IIBbIII", 0, 1, 0, 0, 0, 0, 0),
])
def test_invalid_compact_records(raw):
    with pytest.raises(ArchiveFormatError):
        decode_compact(raw, payload_size=0)


def test_compact_truncation_hashes_and_payload_bounds(snapshot, tmp_path):
    manifest = encode_policy(snapshot, tmp_path / "original.rpw", Policy(), tmp_path / "registry")
    raw = encode_compact(manifest)
    assert decode_compact(raw, payload_size=manifest.payload_size) == manifest
    for stop in (0, 8, 15, len(raw) // 2, len(raw) - 1):
        with pytest.raises(ArchiveFormatError):
            decode_compact(raw[:stop], payload_size=manifest.payload_size)
    with pytest.raises(ArchiveFormatError):
        decode_compact(raw, payload_size=manifest.payload_size + 1)
    first = next(entry for entry in manifest.entries if entry.kind == "file")
    assert bytes.fromhex(first.sha256) in raw
    assert first.sha256.encode() not in raw


def test_compact_policy_rejects_dictionaries():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Policy(metadata_encoding="compact", dictionary_id="a" * 64)
