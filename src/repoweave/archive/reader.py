"""Bounded manifest and payload reads; no settings or service initialization."""

import base64
import os
from collections.abc import Mapping
from typing import BinaryIO

from repoweave.codecs.base import CodecError
from repoweave.codecs.registry import get_codec

from .integrity import check_bytes
from .manifest import HEADER, ArchiveFormatError, FileEntry, Manifest, parse_header, parse_manifest
from .metadata import decode_metadata


def read_manifest(stream: BinaryIO) -> Manifest:
    stream.seek(0)
    size = os.fstat(stream.fileno()).st_size
    header = stream.read(HEADER.size)
    length = parse_header(header)
    data = stream.read(length)
    if len(data) != length:
        raise ArchiveFormatError("Truncated archive manifest")
    if HEADER.unpack(header)[1] == 4:
        return decode_metadata(data, payload_size=size - HEADER.size - length)
    return parse_manifest(data, payload_size=size - HEADER.size - length,
                          version=HEADER.unpack(header)[1])


def decode_file(
    stream: BinaryIO, entry: FileEntry, assets: Mapping[str, str] | None = None,
) -> bytes:
    payload = stream.read(entry.length)
    if len(payload) != entry.length:
        raise ArchiveFormatError("Truncated archive payload")
    try:
        dictionary = {}
        if entry.dictionary_id is not None:
            if assets is None or entry.dictionary_id not in assets:
                raise CodecError("Missing dictionary asset")
            dictionary = {"dictionary": base64.b64decode(assets[entry.dictionary_id], validate=True)}
        data = get_codec(entry.codec).decode(payload, entry.parameters, dictionary, entry.size)
    except CodecError:
        raise ArchiveFormatError("Invalid compressed archive payload") from None
    check_bytes(data, entry.size, entry.sha256)
    return data


def check_archive(stream: BinaryIO) -> Manifest:
    manifest = read_manifest(stream)
    solid = SolidDecoder(stream, manifest) if manifest.blocks else None
    for entry in manifest.entries:
        if isinstance(entry, FileEntry):
            if solid is not None:
                solid.decode(entry)
            else:
                decode_file(stream, entry, manifest.assets)
    if stream.read(1):
        raise ArchiveFormatError("Unexpected trailing archive data")
    return manifest


class SolidDecoder:
    """Sequential file reads sharing only the current validated block."""

    def __init__(self, stream: BinaryIO, manifest: Manifest):
        self.stream = stream
        self.manifest = manifest
        self.block_index = -1
        self.data = b""

    def decode(self, entry: FileEntry) -> bytes:
        if entry.block_index != self.block_index:
            if entry.block_index is None or entry.block_index != self.block_index + 1:
                raise ArchiveFormatError("Invalid solid decode order")
            self.data = b""
            block = self.manifest.blocks[entry.block_index]
            payload = self.stream.read(block.length)
            if len(payload) != block.length:
                raise ArchiveFormatError("Truncated block payload")
            try:
                self.data = get_codec(block.codec).decode(payload, block.parameters, {}, block.size)
            except CodecError:
                raise ArchiveFormatError("Invalid compressed block payload") from None
            self.block_index = entry.block_index
        data = self.data[entry.offset:entry.offset + entry.size]
        check_bytes(data, entry.size, entry.sha256)
        return data
