"""Bounded manifest and payload reads; no settings or service initialization."""

import os
from typing import BinaryIO

from repoweave.codecs.base import CodecError
from repoweave.codecs.registry import get_codec

from .integrity import check_bytes
from .manifest import HEADER, ArchiveFormatError, FileEntry, Manifest, parse_header, parse_manifest


def read_manifest(stream: BinaryIO) -> Manifest:
    stream.seek(0)
    size = os.fstat(stream.fileno()).st_size
    length = parse_header(stream.read(HEADER.size))
    data = stream.read(length)
    if len(data) != length:
        raise ArchiveFormatError("Truncated archive manifest")
    return parse_manifest(data, payload_size=size - HEADER.size - length)


def decode_file(stream: BinaryIO, entry: FileEntry) -> bytes:
    payload = stream.read(entry.length)
    if len(payload) != entry.length:
        raise ArchiveFormatError("Truncated archive payload")
    try:
        data = get_codec(entry.codec).decode(payload, entry.parameters, {}, entry.size)
    except CodecError:
        raise ArchiveFormatError("Invalid compressed archive payload") from None
    check_bytes(data, entry.size, entry.sha256)
    return data


def check_archive(stream: BinaryIO) -> Manifest:
    manifest = read_manifest(stream)
    for entry in manifest.entries:
        if isinstance(entry, FileEntry):
            decode_file(stream, entry)
    if stream.read(1):
        raise ArchiveFormatError("Unexpected trailing archive data")
    return manifest
