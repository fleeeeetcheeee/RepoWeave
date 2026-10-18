import hashlib
import json

import pytest

from repoweave.archive.manifest import (
    HEADER,
    MAGIC,
    MAX_ENCODED_FILE_BYTES,
    MAX_ENTRIES,
    MAX_FILE_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_PAYLOAD_BYTES,
    MAX_TOTAL_BYTES,
    VERSION,
    ArchiveFormatError,
    Manifest,
    encode_manifest,
    parse_header,
    parse_manifest,
)


def file_entry(path: str = "file", **changes: object) -> dict:
    return {
        "kind": "file", "path": path, "size": 0,
        "sha256": hashlib.sha256(b"").hexdigest(), "executable_bits": 0,
        "codec": "stored", "parameters": {}, "offset": 0, "length": 0,
        **changes,
    }


def manifest_bytes(entries: list[dict], **changes: object) -> bytes:
    return json.dumps(
        {"entries": entries, **changes}, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()


def test_empty_archive_known_bytes() -> None:
    header = bytes.fromhex("52505745415645000001000000000000000e")
    assert header == HEADER.pack(MAGIC, VERSION, 14)
    assert parse_header(header) == 14
    manifest = parse_manifest(b'{"entries":[]}', payload_size=0)
    assert encode_manifest(manifest) == b'{"entries":[]}'


def test_mixed_manifest_round_trip_and_unicode() -> None:
    entries = [
        {"kind": "directory", "path": "empty"},
        {"kind": "directory", "path": "src"},
        file_entry("src/a", size=3, length=3, sha256=hashlib.sha256(b"abc").hexdigest()),
        file_entry("src/b", codec="zstd", parameters={"level": 3}, offset=3, length=9),
        file_entry("src/c", codec="lzma", parameters={"preset": 6}, offset=12, length=32),
        file_entry("src/é.py", offset=44, executable_bits=0o111),
    ]
    data = manifest_bytes(entries)
    assert b"\xc3\xa9" in data
    manifest = parse_manifest(data, payload_size=44)
    assert manifest.model_dump(exclude_defaults=True) == {"entries": entries}
    assert manifest.payload_size == 44
    assert encode_manifest(manifest) == data


@pytest.mark.parametrize("header", [
    b"", b"short", HEADER.pack(MAGIC, VERSION, 14) + b"x",
    HEADER.pack(b"WRONG!!!", VERSION, 14), HEADER.pack(MAGIC, 3, 14),
    HEADER.pack(MAGIC, VERSION, 0), HEADER.pack(MAGIC, VERSION, MAX_MANIFEST_BYTES + 1),
])
def test_invalid_headers(header: bytes) -> None:
    with pytest.raises(ArchiveFormatError):
        parse_header(header)


@pytest.mark.parametrize("path", [
    "", "/a", "../a", "a/../b", "a/./b", "a//b", "a/", "C:/a", "C:a",
    "\\\\server\\share", "a\\b", "a\0b", "a\nb", "a\x7fb", "a:b", "a?b",
    "a*b", 'a"b', "a<b", "a>b", "a|b", "a.", "a ", "NUL", "con.txt",
    "COM1.log", "LPT9", "COM¹.txt", "e\u0301", "x" * 256,
    "/".join(["x"] * 65), "/".join(["x" * 250] * 5),
])
def test_unsafe_or_unsupported_paths(path: str) -> None:
    with pytest.raises(ArchiveFormatError):
        parse_manifest(manifest_bytes([file_entry(path)]), payload_size=0)


@pytest.mark.parametrize("entries", [
    [file_entry("a"), file_entry("a")],
    [file_entry("b"), file_entry("a")],
    [file_entry("A"), file_entry("a")],
    [file_entry("SS"), file_entry("ß")],
    [file_entry("missing/child")],
    [file_entry("a"), file_entry("a/child")],
    [{"kind": "directory", "path": "A"}, file_entry("a/child")],
    [{"kind": "directory", "path": "A"}, {"kind": "directory", "path": "a"}],
])
def test_duplicate_colliding_and_invalid_parent_paths(entries: list[dict]) -> None:
    with pytest.raises(ArchiveFormatError):
        parse_manifest(manifest_bytes(entries), payload_size=0)


@pytest.mark.parametrize("changes", [
    {"size": -1}, {"size": True}, {"size": "0"}, {"size": 0.0},
    {"size": MAX_FILE_BYTES + 1}, {"length": -1}, {"length": MAX_ENCODED_FILE_BYTES + 1},
    {"offset": -1}, {"offset": MAX_PAYLOAD_BYTES + 1}, {"offset": 1},
    {"length": 1}, {"executable_bits": 0o644}, {"executable_bits": True},
    {"sha256": "A" * 64}, {"sha256": "a" * 63}, {"sha256": "a" * 64 + "\n"},
    {"codec": "unknown"}, {"kind": "symlink"}, {"asset": "external"},
    {"parameters": {"level": 3}}, {"codec": "zstd", "parameters": {"level": 23}},
    {"codec": "zstd", "parameters": {"level": -8}},
    {"codec": "lzma", "parameters": {"preset": 10}},
    {"codec": "lzma", "parameters": {"preset": True}},
    {"codec": "zstd", "parameters": {"dictionary": 1}},
])
def test_invalid_file_metadata(changes: dict) -> None:
    with pytest.raises(ArchiveFormatError):
        parse_manifest(manifest_bytes([file_entry(**changes)]), payload_size=0)


@pytest.mark.parametrize("data", [
    b"", b"\xff", b"{}", b"[]", b'{"entries":[],"extra":1}',
    b'{"entries":[],"entries":[]}', b'{ "entries": [] }', b'{"entries":[]}\n',
    b'{"entries":null}', b'{"entries":NaN}', b"[" * 2000,
    b'{"entries":[{"kind":"directory","path":"\\ud800"}]}',
    manifest_bytes([{"kind": "directory", "path": "a", "size": 0}]),
])
def test_malformed_or_noncanonical_json(data: bytes) -> None:
    with pytest.raises(ArchiveFormatError):
        parse_manifest(data, payload_size=0)


@pytest.mark.parametrize("offset", [0, 2, 4])
def test_overlapping_out_of_order_or_gapped_payload(offset: int) -> None:
    entries = [file_entry("a", size=3, length=3), file_entry("b", offset=offset, size=2, length=2)]
    with pytest.raises(ArchiveFormatError):
        parse_manifest(manifest_bytes(entries), payload_size=5)


@pytest.mark.parametrize("size", [-1, True, 2, 4, MAX_PAYLOAD_BYTES + 1])
def test_actual_payload_size_must_match(size: int) -> None:
    with pytest.raises(ArchiveFormatError):
        parse_manifest(manifest_bytes([file_entry(size=3, length=3)]), payload_size=size)


def test_aggregate_restored_size_limit() -> None:
    entries = [
        file_entry(f"{i:03}", size=MAX_FILE_BYTES, codec="zstd", offset=i, length=1)
        for i in range(MAX_TOTAL_BYTES // MAX_FILE_BYTES)
    ]
    parse_manifest(manifest_bytes(entries), payload_size=len(entries))
    entries.append(file_entry("zzz", size=1, codec="zstd", offset=len(entries), length=1))
    with pytest.raises(ArchiveFormatError):
        parse_manifest(manifest_bytes(entries), payload_size=len(entries))


def test_manifest_and_entry_count_limits() -> None:
    with pytest.raises(ArchiveFormatError):
        parse_manifest(b" " * (MAX_MANIFEST_BYTES + 1), payload_size=0)
    entries = [{"kind": "directory", "path": f"{i:06}"} for i in range(MAX_ENTRIES + 1)]
    with pytest.raises(ArchiveFormatError):
        parse_manifest(manifest_bytes(entries), payload_size=0)


def test_serialization_revalidates_mutated_collections() -> None:
    manifest = Manifest.model_validate({"entries": [file_entry()]})
    manifest.entries.append(manifest.entries[0])
    with pytest.raises(ArchiveFormatError):
        encode_manifest(manifest)
