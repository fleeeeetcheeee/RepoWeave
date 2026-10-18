import hashlib
import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

from repoweave.archive import decode, filesystem, writer
from repoweave.archive.decode import unpack
from repoweave.archive.encode import pack
from repoweave.archive.integrity import VerificationError, verify
from repoweave.archive.manifest import (
    HEADER,
    MAGIC,
    MAX_FILE_BYTES,
    MAX_MANIFEST_BYTES,
    VERSION,
    ArchiveFormatError,
)
from repoweave.archive.reader import check_archive
from repoweave.archive.writer import write_archive
from repoweave.codecs.registry import get_codec
from repoweave.repository import create_snapshot


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "nested" / "empty-dir").mkdir(parents=True)
    (root / "nested" / "module.py").write_bytes(b"raise RuntimeError('never execute')\r\n" * 1000)
    (root / "nested" / "module.py").chmod(0o751)
    (root / "binary").write_bytes(random.Random(11).randbytes(4096))
    (root / "empty").write_bytes(b"")
    (root / "日本語.md").write_bytes(b"owned synthetic fixture\n")
    (root / ".env").write_bytes(b"SYNTHETIC_EXCLUDED=do-not-archive")
    return root


def raw_archive(path: Path, entries: list[dict], payload: bytes = b"") -> None:
    manifest = json.dumps({"entries": entries}, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":")).encode()
    path.write_bytes(HEADER.pack(MAGIC, VERSION, len(manifest)) + manifest + payload)


def file_entry(path: str, data: bytes, codec: str = "stored", offset: int = 0) -> tuple[dict, bytes]:
    payload = get_codec(codec).encode(data, {}, {})
    return ({"kind": "file", "path": path, "size": len(data),
             "sha256": hashlib.sha256(data).hexdigest(), "executable_bits": 0o011,
             "codec": codec, "parameters": {}, "offset": offset, "length": len(payload)}, payload)


def assert_clean_staging(parent: Path) -> None:
    assert not list(parent.glob(".repoweave-*"))


@pytest.mark.parametrize("codec_id", ["stored", "zstd", "lzma"])
def test_round_trip_and_deterministic_archives(source: Path, tmp_path: Path, codec_id: str) -> None:
    archive = tmp_path / "first.rpw"
    snapshot = pack(source, archive, data_dir=tmp_path / "data", codec_id=codec_id)
    pack(source, tmp_path / "second.rpw", data_dir=tmp_path / "data", codec_id=codec_id)
    assert archive.read_bytes() == (tmp_path / "second.rpw").read_bytes()
    restored = tmp_path / "restored"
    manifest = unpack(archive, restored)
    verify(source, restored)
    assert (restored / "nested" / "empty-dir").is_dir()
    assert not (restored / ".env").exists()
    assert any(item.path == ".env" for item in snapshot.exclusions)
    for entry in snapshot.entries:
        if entry.kind == "file":
            assert (restored / entry.path).read_bytes() == (snapshot.files_root / entry.path).read_bytes()
            assert (restored / entry.path).stat().st_mode & 0o111 == entry.executable_bits
    with archive.open("rb") as stream:
        assert check_archive(stream) == manifest
    assert {item.codec for item in manifest.entries if item.kind == "file"} == (
        {"stored"} if codec_id == "stored" else {"stored", codec_id}
    )
    assert_clean_staging(tmp_path)


def test_mixed_codecs_and_empty_archive(tmp_path: Path) -> None:
    entries, payloads, offset = [], [], 0
    for i, codec in enumerate(("stored", "zstd", "lzma")):
        entry, payload = file_entry(str(i), bytes(range(256)) * 4, codec, offset)
        entries.append(entry)
        payloads.append(payload)
        offset += len(payload)
    archive = tmp_path / "mixed.rpw"
    raw_archive(archive, entries, b"".join(payloads))
    unpack(archive, tmp_path / "mixed")
    for i in range(3):
        assert (tmp_path / "mixed" / str(i)).read_bytes() == bytes(range(256)) * 4
        assert (tmp_path / "mixed" / str(i)).stat().st_mode & 0o111 == 0o011
    raw_archive(tmp_path / "empty.rpw", [])
    unpack(tmp_path / "empty.rpw", tmp_path / "empty")
    assert list((tmp_path / "empty").iterdir()) == []


def test_writer_counts_metadata_when_compressed_payload_alone_is_smaller(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    data = b"x" * 24
    (source / "file").write_bytes(data)
    assert len(get_codec("zstd").encode(data, {"level": 3}, {})) < len(data)
    pack(source, tmp_path / "archive.rpw", data_dir=tmp_path / "data", parameters={"level": 3})
    with (tmp_path / "archive.rpw").open("rb") as stream:
        manifest = check_archive(stream)
    assert manifest.entries[0].codec == "stored"
    assert manifest.entries[0].parameters == {}


def test_writer_uses_snapshot_after_original_changes(source: Path, tmp_path: Path) -> None:
    snapshot = create_snapshot(source, tmp_path / "snapshots")
    (source / "binary").write_bytes(b"changed original")
    write_archive(snapshot, tmp_path / "archive.rpw")
    unpack(tmp_path / "archive.rpw", tmp_path / "restored")
    assert (tmp_path / "restored" / "binary").read_bytes() == (
        snapshot.files_root / "binary"
    ).read_bytes()


@pytest.mark.parametrize("change", ["bytes", "mode", "symlink"])
def test_changed_snapshot_is_rejected(source: Path, tmp_path: Path, change: str) -> None:
    snapshot = create_snapshot(source, tmp_path / "snapshots")
    copied = snapshot.files_root / "binary"
    copied.chmod(0o600)
    if change == "bytes":
        copied.write_bytes(b"different")
    elif change == "mode":
        copied.chmod(0o700)
    else:
        copied.unlink()
        copied.symlink_to(source / "binary")
    output = tmp_path / "archive.rpw"
    with pytest.raises((ArchiveFormatError, OSError)):
        write_archive(snapshot, output)
    assert not output.exists()
    assert_clean_staging(tmp_path)


@pytest.mark.parametrize("path", ["../escape", "/absolute", "C:/escape", "dir/../escape", "NUL.txt"])
def test_unsafe_manifest_never_creates_output(tmp_path: Path, path: str) -> None:
    entry, payload = file_entry(path, b"hello")
    archive = tmp_path / "unsafe.rpw"
    raw_archive(archive, [entry], payload)
    with pytest.raises(ArchiveFormatError):
        unpack(archive, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
    assert not (tmp_path / "escape").exists()
    assert_clean_staging(tmp_path)


@pytest.mark.parametrize("change", [
    {"codec": "missing"}, {"asset": "missing"}, {"parameters": {"dictionary": 7}},
    {"size": MAX_FILE_BYTES + 1}, {"offset": 1}, {"length": -1}, {"executable_bits": 0o7777},
])
def test_invalid_metadata_fails_before_decoding(tmp_path: Path, monkeypatch, change: dict) -> None:
    entry, payload = file_entry("file", b"hello")
    entry.update(change)
    archive = tmp_path / "invalid.rpw"
    raw_archive(archive, [entry], payload)
    monkeypatch.setattr(decode, "decode_file", lambda *args: pytest.fail("must validate first"))
    with pytest.raises(ArchiveFormatError):
        unpack(archive, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_colliding_paths_and_total_limit_fail_before_decoding(tmp_path: Path) -> None:
    a, payload = file_entry("A", b"hello")
    b, other = file_entry("a", b"world", offset=len(payload))
    for entries, data in (
        ([a, b], payload + other),
        ([dict(a, path=f"{i:02}", size=MAX_FILE_BYTES, codec="zstd", length=1, offset=i)
          for i in range(17)], b"x" * 17),
        ([a, dict(b, offset=0)], payload + other),
    ):
        raw_archive(tmp_path / "bad.rpw", entries, data)
        with pytest.raises(ArchiveFormatError):
            unpack(tmp_path / "bad.rpw", tmp_path / "restored")
        assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize("corruption", ["header", "version", "length", "manifest", "payload", "tail", "hash"])
def test_corruption_never_publishes(tmp_path: Path, corruption: str) -> None:
    entry, payload = file_entry("file", b"hello", "zstd")
    archive = tmp_path / "bad.rpw"
    if corruption == "hash":
        entry["sha256"] = "0" * 64
    raw_archive(archive, [entry], payload)
    original = archive.read_bytes()
    mutations = {
        "header": b"bad", "version": HEADER.pack(MAGIC, 2, 1),
        "length": HEADER.pack(MAGIC, VERSION, MAX_MANIFEST_BYTES + 1),
        "manifest": original[:HEADER.size + 1], "payload": original[:-1],
        "tail": original + b"extra", "hash": original,
    }
    archive.write_bytes(mutations[corruption])
    with pytest.raises(ArchiveFormatError):
        unpack(archive, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
    assert_clean_staging(tmp_path)


def test_decompression_bomb_is_bounded(tmp_path: Path) -> None:
    entry, payload = file_entry("file", b"x" * (1024 * 1024), "lzma")
    entry["size"] = 1
    raw_archive(tmp_path / "bomb.rpw", [entry], payload)
    with pytest.raises(ArchiveFormatError):
        unpack(tmp_path / "bomb.rpw", tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
    assert_clean_staging(tmp_path)


@pytest.mark.parametrize("kind", ["file", "directory", "symlink"])
def test_existing_destinations_are_preserved(source: Path, tmp_path: Path, kind: str) -> None:
    archive = tmp_path / "valid.rpw"
    pack(source, archive, data_dir=tmp_path / "data")
    output = tmp_path / "existing"
    if kind == "file":
        output.write_bytes(b"existing")
    elif kind == "directory":
        output.mkdir()
    else:
        output.symlink_to(tmp_path / "missing")
    for operation in (
        lambda: unpack(archive, output),
        lambda: pack(source, output, data_dir=tmp_path / "data"),
    ):
        with pytest.raises(ArchiveFormatError, match="already exists"):
            operation()
    assert output.is_symlink() if kind == "symlink" else output.exists()
    if kind == "file":
        assert output.read_bytes() == b"existing"


@pytest.mark.parametrize("operation", ["pack", "unpack"])
def test_concurrent_destination_is_not_replaced(source: Path, tmp_path: Path, monkeypatch, operation) -> None:
    archive = tmp_path / "valid.rpw"
    pack(source, archive, data_dir=tmp_path / "data")
    output = tmp_path / "raced-output"
    module = writer if operation == "pack" else decode
    original_publish = module.publish
    raced_inode = None

    def race(*args):
        nonlocal raced_inode
        if operation == "pack":
            output.write_bytes(b"other writer")
        else:
            output.mkdir()
            raced_inode = output.stat().st_ino
        return original_publish(*args)

    monkeypatch.setattr(module, "publish", race)
    with pytest.raises(OSError):
        if operation == "pack":
            pack(source, output, data_dir=tmp_path / "data")
        else:
            unpack(archive, output)
    if operation == "pack":
        assert output.read_bytes() == b"other writer"
    else:
        assert output.stat().st_ino == raced_inode
        assert list(output.iterdir()) == []
    assert_clean_staging(tmp_path)


def test_partial_archive_write_is_never_published(source: Path, tmp_path: Path, monkeypatch) -> None:
    def partial_write(source_stream, destination_stream, entry):
        destination_stream.write(source_stream.read(3))
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(writer, "_copy_payload", partial_write)
    with pytest.raises(OSError):
        pack(source, tmp_path / "failed.rpw", data_dir=tmp_path / "data")
    assert not (tmp_path / "failed.rpw").exists()
    assert (source / "binary").read_bytes() == random.Random(11).randbytes(4096)
    assert_clean_staging(tmp_path)


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_writer_and_reader_failure_clean_only_staging(source: Path, tmp_path: Path, monkeypatch, failure) -> None:
    archive = tmp_path / "valid.rpw"
    pack(source, archive, data_dir=tmp_path / "data")
    original_archive = archive.read_bytes()

    def fail(*args, **kwargs):
        raise failure("synthetic failure")

    with monkeypatch.context() as patch:
        patch.setattr(writer, "check_archive", fail)
        with pytest.raises(failure):
            pack(source, tmp_path / "failed.rpw", data_dir=tmp_path / "data")
    original_decode = decode.decode_file
    calls = 0

    def fail_second_file(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            fail()
        return original_decode(*args)

    monkeypatch.setattr(decode, "decode_file", fail_second_file)
    with pytest.raises(failure):
        unpack(archive, tmp_path / "restored")
    assert not (tmp_path / "failed.rpw").exists()
    assert not (tmp_path / "restored").exists()
    assert archive.read_bytes() == original_archive
    assert_clean_staging(tmp_path)


def test_symlink_ancestors_and_archive_links_are_rejected(source: Path, tmp_path: Path) -> None:
    archive = tmp_path / "valid.rpw"
    pack(source, archive, data_dir=tmp_path / "data")
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(outside, target_is_directory=True)
    linked_archive = tmp_path / "link.rpw"
    linked_archive.symlink_to(archive)
    for operation in (
        lambda: unpack(archive, alias / "restored"),
        lambda: pack(source, alias / "output.rpw", data_dir=tmp_path / "data"),
        lambda: unpack(linked_archive, tmp_path / "restored"),
    ):
        with pytest.raises(OSError):
            operation()
    assert list(outside.iterdir()) == []


def test_symlink_inserted_in_staging_is_not_followed(tmp_path: Path, monkeypatch) -> None:
    entry, payload = file_entry("dir/file", b"hello")
    raw_archive(tmp_path / "valid.rpw", [{"kind": "directory", "path": "dir"}, entry], payload)
    outside = tmp_path / "outside"
    outside.mkdir()
    original_open = decode.open_directory

    def replace_directory(path, **kwargs):
        if path == Path("dir") and "base_fd" in kwargs:
            fd = kwargs["base_fd"]
            os.rmdir("dir", dir_fd=fd)
            os.symlink(outside, "dir", dir_fd=fd)
        return original_open(path, **kwargs)

    monkeypatch.setattr(decode, "open_directory", replace_directory)
    with pytest.raises(OSError):
        unpack(tmp_path / "valid.rpw", tmp_path / "restored")
    assert list(outside.iterdir()) == []
    assert not (tmp_path / "restored").exists()
    assert_clean_staging(tmp_path)


@pytest.mark.parametrize("change", ["missing", "extra", "bytes", "mode", "empty-directory"])
def test_verify_detects_supported_differences(source: Path, tmp_path: Path, change: str) -> None:
    pack(source, tmp_path / "valid.rpw", data_dir=tmp_path / "data")
    output = tmp_path / "restored"
    unpack(tmp_path / "valid.rpw", output)
    if change == "missing":
        (output / "binary").unlink()
    elif change == "extra":
        (output / "extra").write_bytes(b"extra")
    elif change == "bytes":
        (output / "binary").write_bytes(b"changed")
    elif change == "mode":
        (output / "binary").chmod(0o700)
    else:
        (output / "nested" / "empty-dir").rmdir()
    with pytest.raises(VerificationError):
        verify(source, output)


def test_custom_output_exclusions_and_nested_restored_directory(source: Path, tmp_path: Path) -> None:
    data_dir = source / "custom-data"
    archive = source / "reports" / "example.rpw"
    snapshot = pack(source, archive, data_dir=data_dir)
    output = data_dir / "restored"
    unpack(archive, output)
    assert {item.path for item in snapshot.exclusions} == {".env", "custom-data", "reports"}
    verify(source, output, data_dir=data_dir, exclude_paths=("reports",))


def test_unsupported_snapshot_names_fail_without_renaming(source: Path, tmp_path: Path) -> None:
    (source / "NUL.txt").write_bytes(b"owned bytes")
    with pytest.raises(ArchiveFormatError, match="unsupported"):
        pack(source, tmp_path / "failed.rpw", data_dir=tmp_path / "data")
    assert not (tmp_path / "failed.rpw").exists()
    assert (source / "NUL.txt").read_bytes() == b"owned bytes"


def test_unsupported_platform_cannot_publish(source: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(filesystem.sys, "platform", "unsupported")
    with pytest.raises(ArchiveFormatError, match="unsupported"):
        pack(source, tmp_path / "failed.rpw", data_dir=tmp_path / "data")
    assert not (tmp_path / "failed.rpw").exists()
    assert_clean_staging(tmp_path)


def test_clean_decoder_process_has_no_settings_or_service_imports(source: Path, tmp_path: Path) -> None:
    archive = tmp_path / "valid.rpw"
    pack(source, archive, data_dir=tmp_path / "data")
    result = subprocess.run([sys.executable, "-c", """
import sys
from pathlib import Path
from repoweave.archive.decode import unpack
unpack(Path(sys.argv[1]), Path(sys.argv[2]))
assert not any(name.startswith(('repoweave.config', 'repoweave.agents',
    'repoweave.workflows', 'daytona', 'langsmith', 'langgraph', 'dotenv')) for name in sys.modules)
""", str(archive), str(tmp_path / "restored")], cwd=tmp_path, env={},
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
