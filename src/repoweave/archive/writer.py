"""Write deterministic archives from version 2 snapshot metadata and copied bytes."""

import base64
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path

from pydantic import ValidationError

from repoweave.codecs.base import validate_options
from repoweave.codecs.registry import get_codec
from repoweave.repository import Snapshot

from .filesystem import open_directory, open_regular, publish, require_absent, staging_directory
from .integrity import check_bytes
from .manifest import (
    HEADER,
    MAGIC,
    VERSION,
    ArchiveFormatError,
    DirectoryEntry,
    FileEntry,
    Manifest,
    encode_manifest,
)
from .reader import check_archive


def _snapshot_manifest(snapshot: Snapshot) -> Manifest:
    entries: list[dict[str, object]] = []
    offset = 0
    for entry in snapshot.entries:
        if entry.kind == "directory":
            entries.append({"kind": "directory", "path": entry.path})
        else:
            entries.append({
                "kind": "file", "path": entry.path, "size": entry.size_bytes,
                "sha256": entry.sha256, "executable_bits": entry.executable_bits,
                "codec": "stored", "parameters": {}, "offset": offset, "length": entry.size_bytes,
            })
            if type(entry.size_bytes) is int:
                offset += entry.size_bytes
    try:
        manifest = Manifest.model_validate({"entries": entries})
        encode_manifest(manifest)
        return manifest
    except (ValidationError, ArchiveFormatError):
        raise ArchiveFormatError("Snapshot paths, metadata, or sizes are unsupported by RPW V1") from None


def _entry_bytes(entry: FileEntry) -> int:
    return len(json.dumps(
        entry.model_dump(exclude_defaults=True), sort_keys=True,
        ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8"))


def write_archive(
    snapshot: Snapshot, output: Path, *, codec_id: str = "zstd",
    parameters: Mapping[str, int] | None = None,
    dictionaries: Mapping[str, bytes] | None = None,
) -> Manifest:
    parameters = {} if parameters is None else dict(parameters)
    validate_options(codec_id, parameters, {})
    codec = get_codec(codec_id)
    if dictionaries and codec_id != "zstd":
        raise ArchiveFormatError("Dictionaries require Zstd encoding")
    dictionaries = {} if dictionaries is None else dictionaries
    for asset in dictionaries.values():
        validate_options("zstd_dictionary", parameters, {"dictionary": asset})
    original = _snapshot_manifest(snapshot)
    with open_directory(output.parent, create=True) as parent:
        require_absent(parent, output.name)
        with staging_directory(parent) as (_, staging), tempfile.TemporaryFile() as payloads:
            entries: list[FileEntry | DirectoryEntry] = []
            assets: dict[str, str] = {}
            for entry in original.entries:
                if isinstance(entry, DirectoryEntry):
                    entries.append(entry)
                    continue
                with open_regular(snapshot.files_root / entry.path) as source:
                    data = source.read(entry.size + 1)
                    if os.fstat(source.fileno()).st_mode & 0o111 != entry.executable_bits:
                        raise ArchiveFormatError("Snapshot executable bits changed")
                check_bytes(data, entry.size, entry.sha256)
                dictionary = dictionaries.get(entry.path)
                selected_codec = "zstd_dictionary" if dictionary is not None else codec_id
                digest = hashlib.sha256(dictionary).hexdigest() if dictionary is not None else None
                compressed = (get_codec(selected_codec).encode(
                    data, parameters, {"dictionary": dictionary},
                ) if dictionary is not None else codec.encode(data, parameters, {}))
                stored = entry.model_copy(update={"offset": payloads.tell()})
                candidate = stored.model_copy(update={
                    "codec": selected_codec, "parameters": parameters, "length": len(compressed),
                    "dictionary_id": digest,
                })
                new_assets = dict(assets)
                if digest is not None and dictionary is not None:
                    new_assets[digest] = base64.b64encode(dictionary).decode("ascii")
                # shortcut: charge first use fully; amortize groups if small-file benchmarks justify it.
                asset_cost = len(json.dumps(new_assets, separators=(",", ":"))) - len(
                    json.dumps(assets, separators=(",", ":")))
                if new_assets and not assets:
                    asset_cost += len(',"assets":{}')
                if (len(compressed) + _entry_bytes(candidate) + asset_cost
                        < len(data) + _entry_bytes(stored)):
                    assets = new_assets
                    entries.append(candidate)
                    payloads.write(compressed)
                else:
                    entries.append(stored)
                    payloads.write(data)
            manifest = Manifest(entries=entries, assets=assets)
            encoded = encode_manifest(manifest)
            fd = os.open("archive", os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=staging)
            with os.fdopen(fd, "w+b") as archive:
                archive.write(HEADER.pack(MAGIC, 2 if assets else VERSION, len(encoded)))
                archive.write(encoded)
                payloads.seek(0)
                shutil.copyfileobj(payloads, archive, length=1024 * 1024)
                archive.flush()
                os.fsync(archive.fileno())
                check_archive(archive)
            publish(staging, "archive", parent, output.name)
            return manifest
