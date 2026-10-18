"""Write deterministic archives from version 2 snapshot metadata and copied bytes."""

import base64
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from pydantic import ValidationError

from repoweave.codecs.base import MAX_FILE_BYTES, validate_options
from repoweave.codecs.registry import get_codec
from repoweave.repository import Snapshot

from .filesystem import open_directory, open_regular, publish, require_absent, staging_directory
from .integrity import check_bytes
from .manifest import (
    HEADER,
    MAGIC,
    ArchiveFormatError,
    Block,
    DirectoryEntry,
    FileEntry,
    Manifest,
    encode_manifest,
)
from .metadata import encode_metadata
from .reader import check_archive
from .solid import build_solid


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


def _layout(entries: list[FileEntry | DirectoryEntry], assets: dict[str, str]) -> Manifest:
    offset = 0
    positioned: list[FileEntry | DirectoryEntry] = []
    for entry in entries:
        if isinstance(entry, FileEntry):
            positioned.append(entry.model_copy(update={"offset": offset}))
            offset += entry.length
        else:
            positioned.append(entry)
    return Manifest(entries=positioned, assets=assets)


def _copy_payload(source, destination, entry: FileEntry | Block) -> None:
    source.seek(entry.offset)
    remaining = entry.length
    while remaining:
        chunk = source.read(min(remaining, 1024 * 1024))
        if not chunk:
            raise ArchiveFormatError("Truncated payload spool")
        destination.write(chunk)
        remaining -= len(chunk)


def write_archive(
    snapshot: Snapshot, output: Path, *, codec_id: str = "zstd",
    parameters: Mapping[str, int] | None = None,
    dictionaries: Mapping[str, bytes] | None = None,
    solid_block_bytes: int | None = None,
    metadata_encoding: str = "json",
) -> Manifest:
    parameters = {} if parameters is None else dict(parameters)
    validate_options(codec_id, parameters, {})
    codec = get_codec(codec_id)
    if solid_block_bytes is not None and (
        type(solid_block_bytes) is not int or not 1 <= solid_block_bytes <= MAX_FILE_BYTES
        or codec_id not in ("zstd", "lzma") or dictionaries
    ):
        raise ArchiveFormatError("Solid blocks require bounded Zstd/LZMA without dictionaries")
    if metadata_encoding not in ("json", "compressed", "compact"):
        raise ArchiveFormatError("Unsupported metadata encoding")
    if metadata_encoding == "compact" and dictionaries:
        raise ArchiveFormatError("Compact metadata does not support dictionary assets")
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
            alternatives: dict[str, FileEntry] = {}
            savings: dict[str, int] = {}
            baseline_offset = 0
            for entry in original.entries:
                if isinstance(entry, DirectoryEntry):
                    entries.append(entry)
                    continue
                with open_regular(snapshot.files_root / entry.path) as source:
                    data = source.read(entry.size + 1)
                    if os.fstat(source.fileno()).st_mode & 0o111 != entry.executable_bits:
                        raise ArchiveFormatError("Snapshot executable bits changed")
                check_bytes(data, entry.size, entry.sha256)
                compressed = codec.encode(data, parameters, {})
                stored = entry.model_copy(update={"offset": baseline_offset})
                candidate = stored.model_copy(update={
                    "codec": codec_id, "parameters": parameters, "length": len(compressed),
                })
                if (len(compressed) + _entry_bytes(candidate)
                        < len(data) + _entry_bytes(stored)):
                    selected, encoded_data = candidate, compressed
                else:
                    selected, encoded_data = stored, data
                # Temporary offsets refer to the spool; final offsets are assigned by _layout.
                entries.append(selected.model_copy(update={"offset": payloads.tell()}))
                payloads.write(encoded_data)
                baseline_offset += selected.length
                dictionary = dictionaries.get(entry.path)
                if dictionary is not None:
                    digest = hashlib.sha256(dictionary).hexdigest()
                    compressed = get_codec("zstd_dictionary").encode(
                        data, parameters, {"dictionary": dictionary})
                    candidate = selected.model_copy(update={
                        "codec": "zstd_dictionary", "parameters": parameters,
                        "length": len(compressed), "dictionary_id": digest,
                    })
                    saving = (selected.length + _entry_bytes(selected)
                              - candidate.length - _entry_bytes(candidate))
                    if saving > 0:
                        alternatives[entry.path] = candidate.model_copy(
                            update={"offset": payloads.tell()})
                        payloads.write(compressed)
                        savings[digest] = savings.get(digest, 0) + saving
                        assets[digest] = base64.b64encode(dictionary).decode("ascii")
            # Charge each shared asset once against group-wide savings over plain zstd/stored.
            assets = {key: value for key, value in assets.items() if savings[key] > len(
                json.dumps({key: value}, separators=(",", ":"))) + len(',"assets":{}')}
            selected_entries = [alternatives[entry.path] if entry.path in alternatives
                                and alternatives[entry.path].dictionary_id in assets else entry
                                for entry in entries]
            plain = _layout(entries, {})
            manifest = _layout(selected_entries, assets)
            try:
                candidate_size: int | float = (
                    len(encode_metadata(manifest, metadata_encoding)[1]) + manifest.payload_size
                )
            except ArchiveFormatError:
                candidate_size = float("inf")  # Excessive asset metadata falls back to plain.
            plain_size = len(encode_metadata(plain, metadata_encoding)[1]) + plain.payload_size
            if candidate_size >= plain_size:
                manifest, selected_entries = plain, entries
            selected_payloads: list[FileEntry | Block] = [
                entry for entry in selected_entries if isinstance(entry, FileEntry)
            ]
            if solid_block_bytes is not None:
                solid, block_payloads = build_solid(
                    snapshot, original, parameters, solid_block_bytes, payloads, codec_id=codec_id,
                )
                try:
                    solid_size: int | float = (
                        len(encode_metadata(solid, metadata_encoding)[1]) + solid.payload_size
                    )
                except ArchiveFormatError:
                    solid_size = float("inf")
                if solid.blocks and solid_size < plain_size:
                    manifest = solid
                    selected_payloads = list(block_payloads)
            version, encoded = encode_metadata(manifest, metadata_encoding)
            fd = os.open("archive", os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=staging)
            with os.fdopen(fd, "w+b") as archive:
                archive.write(HEADER.pack(MAGIC, version, len(encoded)))
                archive.write(encoded)
                for payload in selected_payloads:
                    _copy_payload(payloads, archive, payload)
                archive.flush()
                os.fsync(archive.fileno())
                check_archive(archive)
            publish(staging, "archive", parent, output.name)
            return manifest
