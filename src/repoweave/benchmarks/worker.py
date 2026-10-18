"""Child-only measurements; input source is read as bytes, never imported or executed."""

import base64
import io
import lzma
import os
import resource
import sys
import tarfile
import time
from contextlib import ExitStack
from pathlib import Path

import zstandard

from repoweave.archive.decode import unpack
from repoweave.archive.filesystem import open_regular
from repoweave.archive.integrity import check_bytes
from repoweave.archive.manifest import HEADER, FileEntry
from repoweave.archive.reader import read_manifest
from repoweave.archive.writer import _snapshot_manifest
from repoweave.repository import Snapshot
from repoweave.routing.encode import encode_policy
from repoweave.routing.schemas import Policy


def _tar_encode(snapshot: Snapshot, output: Path, codec: str) -> None:
    manifest = _snapshot_manifest(snapshot)
    with ExitStack() as stack:
        raw = stack.enter_context(output.open("xb"))
        compressed = stack.enter_context(
            zstandard.ZstdCompressor(level=3, write_checksum=True).stream_writer(raw, closefd=False)
            if codec == "zstd" else stack.enter_context(lzma.LZMAFile(raw, "w", preset=6))
        )
        archive = stack.enter_context(tarfile.open(fileobj=compressed, mode="w|",
                                                 format=tarfile.PAX_FORMAT))
        for entry in manifest.entries:
            info = tarfile.TarInfo(entry.path)
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ""
            if isinstance(entry, FileEntry):
                info.size = entry.size
                info.mode = 0o600 | entry.executable_bits
                with open_regular(snapshot.files_root / entry.path) as source:
                    data = source.read(entry.size + 1)
                    check_bytes(data, entry.size, entry.sha256)
                    if os.fstat(source.fileno()).st_mode & 0o111 != entry.executable_bits:
                        raise ValueError("Snapshot executable bits changed")
                    archive.addfile(info, io.BytesIO(data))
            else:
                info.type = tarfile.DIRTYPE
                info.mode = 0o700
                archive.addfile(info)
    with open_regular(output) as source:
        os.fsync(source.fileno())


def _tar_decode(snapshot: Snapshot, archive: Path, output: Path, codec: str) -> None:
    manifest = _snapshot_manifest(snapshot)
    output.mkdir()
    with ExitStack() as stack:
        raw = stack.enter_context(open_regular(archive))
        decompressed = stack.enter_context(
            zstandard.ZstdDecompressor().stream_reader(raw)
            if codec == "zstd" else stack.enter_context(lzma.LZMAFile(raw))
        )
        tar = stack.enter_context(tarfile.open(fileobj=decompressed, mode="r|"))
        for entry in manifest.entries:
            member = tar.next()
            if member is None or member.name != entry.path:
                raise ValueError("Tar inventory mismatch")
            target = output / entry.path
            if isinstance(entry, FileEntry):
                if (not member.isfile() or member.size != entry.size
                        or member.mode != 0o600 | entry.executable_bits):
                    raise ValueError("Tar metadata mismatch")
                source = tar.extractfile(member)
                assert source is not None
                with source, target.open("xb") as destination:
                    remaining = entry.size
                    while remaining:
                        chunk = source.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            raise ValueError("Truncated tar file")
                        destination.write(chunk)
                        remaining -= len(chunk)
                target.chmod(0o600 | entry.executable_bits)
            else:
                if not member.isdir():
                    raise ValueError("Tar directory mismatch")
                target.mkdir()
        if tar.next() is not None:
            raise ValueError("Unexpected tar entry")


def _compare(snapshot: Snapshot, restored: Path) -> None:
    expected = {entry.path for entry in snapshot.entries}
    actual = {path.relative_to(restored).as_posix() for path in restored.rglob("*")}
    if actual != expected:
        raise ValueError("Restored inventory differs")
    for entry in snapshot.entries:
        path = restored / entry.path
        if entry.kind == "directory":
            if not path.is_dir() or path.is_symlink():
                raise ValueError("Restored directory differs")
            continue
        with open_regular(snapshot.files_root / entry.path) as original, open_regular(path) as decoded:
            if os.fstat(decoded.fileno()).st_mode & 0o111 != entry.executable_bits:
                raise ValueError("Restored executable bits differ")
            while True:
                data = original.read(1024 * 1024)
                if decoded.read(len(data) or 1) != data:
                    raise ValueError("Restored bytes differ")
                if not data:
                    break


def measure(connection, snapshot: Snapshot, artifact: Path, restored: Path, method: str,
            phase: str, policy: Policy, registry: Path) -> None:
    try:
        started = time.perf_counter()
        if phase == "encode":
            if method == "rpw":
                encode_policy(snapshot, artifact, policy, registry)
            else:
                _tar_encode(snapshot, artifact, method.removeprefix("tar-"))
        else:
            if method == "rpw":
                unpack(artifact, restored)
            else:
                _tar_decode(snapshot, artifact, restored, method.removeprefix("tar-"))
            _compare(snapshot, restored)
        elapsed = time.perf_counter() - started
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result = {"status": "ok", "seconds": elapsed,
                  "peak_rss_bytes": int(peak if sys.platform == "darwin" else peak * 1024)}
        if phase == "encode":
            result["archive_bytes"] = artifact.stat().st_size
            if method == "rpw":
                with open_regular(artifact) as stream:
                    manifest = read_manifest(stream)
                    result["manifest_bytes"] = stream.tell() - HEADER.size
                result["payload_bytes"] = manifest.payload_size
                result["embedded_dictionary_bytes"] = sum(
                    len(base64.b64decode(value)) for value in manifest.assets.values()
                )
                result["dependencies"] = sorted(manifest.assets)
        connection.send(result)
    except Exception as error:  # noqa: BLE001 - child failure boundary must not leak source in tracebacks
        # Exceptions can contain source snippets or paths; persist only the failure class.
        connection.send({"status": "failed", "error": type(error).__name__})
    finally:
        connection.close()
