import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_portable_dictionary_archive_and_old_policy_independence(tmp_path: Path, dictionary: bytes) -> None:
    data = b"def function_21(value):\n    return value + 21\n" * 1000
    captured = snapshot(tmp_path, data)
    archive = tmp_path / "dictionary.rpw"
    manifest = write_archive(captured, archive, dictionaries={"file": dictionary})
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
    assert (tmp_path / "restored/file").stat().st_mode & 0o111 == 0o111
    v1 = tmp_path / "plain.rpw"
    write_archive(captured, v1)
    assert HEADER.unpack(v1.read_bytes()[:HEADER.size])[1] == 1
    unpack(v1, tmp_path / "plain-restored")
    assert (tmp_path / "plain-restored/file").read_bytes() == data


def test_dictionary_storage_overhead_forces_stored_fallback(tmp_path: Path, dictionary: bytes) -> None:
    captured = snapshot(tmp_path, b"def function_21(value):\n    return value + 21\n" * 10)
    archive = tmp_path / "small.rpw"
    manifest = write_archive(captured, archive, dictionaries={"file": dictionary})
    assert manifest.entries[0].codec == "stored"
    assert manifest.assets == {}
    assert HEADER.unpack(archive.read_bytes()[:HEADER.size])[1] == 1


@pytest.mark.parametrize("damage", ["missing", "hash", "invalid", "unused", "reference", "v1", "version"])
def test_bad_dictionary_archives_never_publish(tmp_path: Path, dictionary: bytes, damage: str) -> None:
    captured = snapshot(tmp_path, b"source line\n" * 1000)
    archive = tmp_path / "valid.rpw"
    write_archive(captured, archive, dictionaries={"file": dictionary})
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
