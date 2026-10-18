import base64
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest
import zstandard

from repoweave.archive.decode import unpack
from repoweave.archive.manifest import HEADER, MAGIC, ArchiveFormatError, encode_manifest
from repoweave.archive.reader import read_manifest
from repoweave.archive.writer import write_archive
from repoweave.repository import create_snapshot
from repoweave.routing.schemas import Policy


def snapshot(tmp_path: Path, data: bytes):
    root = tmp_path / "input"
    root.mkdir()
    (root / "file").write_bytes(data)
    (root / "file").chmod(0o751)
    return create_snapshot(root, tmp_path / "snapshots")


def shared_fixture(tmp_path):
    common = random.Random(1).randbytes(800)
    dictionary = zstandard.train_dictionary(1024, [
        common + random.Random(index).randbytes(100) for index in range(50)
    ], threads=0).as_bytes()
    root = tmp_path / "input"
    root.mkdir()
    for index in range(32):
        (root / ("file" if index == 0 else f"file{index:02d}")).write_bytes(
            common + random.Random(1000 + index).randbytes(100))
    (root / "file").chmod(0o751)
    return create_snapshot(root, tmp_path / "snapshots"), dictionary


def test_compressed_json_metadata_keeps_dictionary_portable(tmp_path):
    from repoweave.archive.decode import unpack
    from repoweave.benchmarks.worker import _compare

    captured, dictionary = shared_fixture(tmp_path)
    archive = tmp_path / "compressed-dictionary.rpw"
    manifest = write_archive(captured, archive, metadata_encoding="compressed",
                             dictionaries={entry.path: dictionary for entry in captured.entries})
    assert manifest.assets
    assert HEADER.unpack(archive.read_bytes()[:HEADER.size])[1] == 4
    assert unpack(archive, tmp_path / "restored") == manifest
    _compare(captured, tmp_path / "restored")


def test_portable_dictionary_archive_and_old_policy_independence(tmp_path: Path) -> None:
    captured, dictionary = shared_fixture(tmp_path)
    data = (captured.files_root / "file").read_bytes()
    archive = tmp_path / "dictionary.rpw"
    manifest = write_archive(captured, archive,
                             dictionaries={entry.path: dictionary for entry in captured.entries})
    assert manifest.entries[0].codec == "zstd_dictionary"
    assert len(manifest.assets) == 1
    assert archive.stat().st_size == HEADER.size + len(encode_manifest(manifest)) + manifest.payload_size
    assert archive.stat().st_size > len(dictionary) + manifest.payload_size
    assert HEADER.unpack(archive.read_bytes()[:HEADER.size])[1] == 2
    Policy(zstd_level=12).save(tmp_path / "policies")
    result = subprocess.run([sys.executable, "-c", """
import sys
from pathlib import Path
from repoweave.archive.decode import unpack
unpack(Path(sys.argv[1]), Path(sys.argv[2]))
assert not any(name.startswith(('repoweave.config', 'repoweave.dictionaries', 'repoweave.routing',
    'repoweave.agents', 'langgraph', 'daytona', 'dotenv')) for name in sys.modules)
""", str(archive), str(tmp_path / "restored")], cwd=tmp_path, env={}, capture_output=True,
                            text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "restored/file").read_bytes() == data
    for entry in captured.entries:
        assert (tmp_path / "restored" / entry.path).read_bytes() == (
            captured.files_root / entry.path).read_bytes()
    assert (tmp_path / "restored/file").stat().st_mode & 0o111 == 0o111
    v1 = tmp_path / "plain.rpw"
    write_archive(captured, v1)
    assert archive.stat().st_size < v1.stat().st_size
    assert HEADER.unpack(v1.read_bytes()[:HEADER.size])[1] == 1
    unpack(v1, tmp_path / "plain-restored")
    assert (tmp_path / "plain-restored/file").read_bytes() == data


def test_group_amortization_does_not_charge_a_single_small_file(tmp_path: Path) -> None:
    captured, dictionary = shared_fixture(tmp_path)
    single = tmp_path / "single"
    single.mkdir()
    (single / "file").write_bytes((captured.files_root / "file").read_bytes())
    small = create_snapshot(single, tmp_path / "small-captures")
    plain, candidate = tmp_path / "plain.rpw", tmp_path / "candidate.rpw"
    write_archive(small, plain)
    write_archive(small, candidate, dictionaries={"file": dictionary})
    assert candidate.read_bytes() == plain.read_bytes()


def test_dictionary_storage_overhead_falls_back_to_plain_zstd(tmp_path: Path, dictionary: bytes) -> None:
    captured = snapshot(tmp_path, b"def function_21(value):\n    return value + 21\n" * 10)
    archive = tmp_path / "small.rpw"
    manifest = write_archive(captured, archive, dictionaries={"file": dictionary})
    assert manifest.entries[0].codec == "zstd"
    assert manifest.assets == {}
    assert HEADER.unpack(archive.read_bytes()[:HEADER.size])[1] == 1
    plain = tmp_path / "plain.rpw"
    write_archive(captured, plain)
    assert plain.read_bytes() == archive.read_bytes()


@pytest.mark.parametrize("damage", ["missing", "hash", "invalid", "unused", "reference", "v1", "version"])
def test_bad_dictionary_archives_never_publish(tmp_path: Path, damage: str) -> None:
    captured, dictionary = shared_fixture(tmp_path)
    archive = tmp_path / "valid.rpw"
    write_archive(captured, archive,
                  dictionaries={entry.path: dictionary for entry in captured.entries})
    with archive.open("rb") as source:
        manifest = read_manifest(source)
        payload = source.read()
    value = manifest.model_dump(exclude_defaults=True)
    digest = next(iter(value["assets"]))
    version = 2
    if damage == "missing":
        value["assets"] = {}
    elif damage == "hash":
        value["assets"][digest] = base64.b64encode(b"wrong").decode()
    elif damage == "invalid":
        value["assets"][digest] = "!!!!"
    elif damage == "unused":
        value["assets"]["a" * 64] = value["assets"][digest]
    elif damage == "reference":
        value["entries"][0]["dictionary_id"] = "b" * 64
    else:
        version = 1 if damage == "v1" else 99
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    damaged = tmp_path / "damaged.rpw"
    damaged.write_bytes(HEADER.pack(MAGIC, version, len(encoded)) + encoded + payload)
    with pytest.raises(ArchiveFormatError):
        unpack(damaged, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
    assert not list(tmp_path.glob(".repoweave-*"))
