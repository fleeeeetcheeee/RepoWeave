"""V4 bounded wire metadata; legacy canonical JSON remains the logical schema."""

import io
import struct

from repoweave.codecs.base import CodecError
from repoweave.codecs.registry import get_codec

from .manifest import (
    MAX_ENTRIES,
    MAX_MANIFEST_BYTES,
    MAX_PAYLOAD_BYTES,
    ArchiveFormatError,
    FileEntry,
    Manifest,
    encode_manifest,
    parse_manifest,
)

PREFIX = struct.Struct(">BBI")
CODECS = ("stored", "zstd", "lzma")


def encode_compact(manifest: Manifest) -> bytes:
    """Canonical fixed-width records with prefix paths and binary SHA-256 values."""
    encode_manifest(manifest)  # Revalidate mutable collections and the expanded-size limit.
    if manifest.assets:
        raise ArchiveFormatError("Compact metadata does not support dictionary assets")
    output = bytearray(struct.pack(">II", len(manifest.entries), len(manifest.blocks)))
    for block in manifest.blocks:
        option = next(iter(block.parameters.values()), -128)
        output.extend(struct.pack(">BbIII", CODECS.index(block.codec), option,
                                  block.size, block.offset, block.length))
    previous = b""
    for entry in manifest.entries:
        path = entry.path.encode("utf-8")
        common = 0
        for left, right in zip(previous, path, strict=False):
            if left != right:
                break
            common += 1
        suffix = path[common:]
        output.extend(struct.pack(">HH", common, len(suffix)))
        output.extend(suffix)
        previous = path
        if not isinstance(entry, FileEntry):
            output.append(0)
            continue
        output.append(2 if entry.codec == "solid" else 1)
        output.extend(struct.pack(">IB32s", entry.size, entry.executable_bits,
                                  bytes.fromhex(entry.sha256)))
        if entry.codec == "solid":
            output.extend(struct.pack(">II", entry.block_index, entry.offset))
        else:
            option = next(iter(entry.parameters.values()), -128)
            output.extend(struct.pack(">BbII", CODECS.index(entry.codec), option,
                                      entry.offset, entry.length))
    return bytes(output)


def decode_compact(data: bytes, *, payload_size: int) -> Manifest:
    if not 0 < len(data) <= MAX_MANIFEST_BYTES:
        raise ArchiveFormatError("Invalid compact metadata size")
    if type(payload_size) is not int or not 0 <= payload_size <= MAX_PAYLOAD_BYTES:
        raise ArchiveFormatError("Invalid payload length")
    stream = io.BytesIO(data)

    def read(size: int) -> bytes:
        result = stream.read(size)
        if len(result) != size:
            raise ArchiveFormatError("Truncated compact metadata")
        return result

    def fields(layout: str) -> tuple:
        return struct.unpack(layout, read(struct.calcsize(layout)))

    def codec_fields(code: int, option: int) -> tuple[str, dict[str, int]]:
        if code >= len(CODECS) or (code == 0 and option != -128):
            raise ArchiveFormatError("Invalid compact codec")
        codec = CODECS[code]
        return codec, ({} if option == -128 else {"level" if code == 1 else "preset": option})

    try:
        entry_count, block_count = fields(">II")
        if entry_count > MAX_ENTRIES or block_count > MAX_ENTRIES:
            raise ArchiveFormatError("Excessive compact record count")
        blocks = []
        for _ in range(block_count):
            code, option, size, offset, length = fields(">BbIII")
            codec, parameters = codec_fields(code, option)
            blocks.append({"codec": codec, "parameters": parameters, "size": size,
                           "offset": offset, "length": length})
        entries = []
        previous = b""
        for _ in range(entry_count):
            common, suffix_size = fields(">HH")
            if common > len(previous) or common + suffix_size > 1024:
                raise ArchiveFormatError("Invalid compact path lengths")
            path = previous[:common] + read(suffix_size)
            previous = path
            kind, = fields(">B")
            if kind not in (0, 1, 2):
                raise ArchiveFormatError("Invalid compact entry kind")
            entry: dict = {"kind": "directory" if kind == 0 else "file", "path": path.decode("utf-8")}
            if kind:
                size, bits, digest = fields(">IB32s")
                entry.update(size=size, executable_bits=bits, sha256=digest.hex())
                if kind == 2:
                    block_index, offset = fields(">II")
                    entry.update(codec="solid", parameters={}, block_index=block_index,
                                 offset=offset, length=size)
                else:
                    code, option, offset, length = fields(">BbII")
                    codec, parameters = codec_fields(code, option)
                    entry.update(codec=codec, parameters=parameters, offset=offset, length=length)
            entries.append(entry)
        if stream.read(1):
            raise ArchiveFormatError("Trailing compact metadata")
        manifest = Manifest.model_validate({"entries": entries, "blocks": blocks})
        if manifest.payload_size != payload_size or encode_compact(manifest) != data:
            raise ArchiveFormatError("Noncanonical compact metadata or payload length")
        return manifest
    except (ValueError, UnicodeError, struct.error):
        raise ArchiveFormatError("Invalid compact metadata") from None


def encode_metadata(manifest: Manifest, encoding: str = "json") -> tuple[int, bytes]:
    """Select the smallest permitted representation, including envelope overhead."""
    if encoding not in ("json", "compressed", "compact"):
        raise ArchiveFormatError("Unsupported metadata encoding")
    raw = encode_manifest(manifest)
    requires_v4 = any(block.codec == "lzma" for block in manifest.blocks)
    version = 3 if manifest.blocks else 2 if manifest.assets else 1
    candidates = [(4, PREFIX.pack(0, 0, len(raw)) + raw)] if requires_v4 else [(version, raw)]
    if encoding in ("compressed", "compact"):
        compressed = get_codec("zstd").encode(raw, {"level": 3}, {})
        candidates.append((4, PREFIX.pack(0, 1, len(raw)) + compressed))
    if encoding == "compact":
        compact = encode_compact(manifest)
        candidates.append((4, PREFIX.pack(1, 0, len(compact)) + compact))
        compressed = get_codec("zstd").encode(compact, {"level": 3}, {})
        candidates.append((4, PREFIX.pack(1, 1, len(compact)) + compressed))
    valid = [candidate for candidate in candidates if len(candidate[1]) <= MAX_MANIFEST_BYTES]
    if not valid:
        raise ArchiveFormatError("Wire metadata exceeds byte limit")
    return min(valid, key=lambda candidate: len(candidate[1]))


def decode_metadata(data: bytes, *, payload_size: int) -> Manifest:
    if type(payload_size) is not int or not 0 <= payload_size <= MAX_PAYLOAD_BYTES:
        raise ArchiveFormatError("Invalid payload length")
    if not PREFIX.size < len(data) <= MAX_MANIFEST_BYTES:
        raise ArchiveFormatError("Invalid wire metadata length")
    representation, compression, size = PREFIX.unpack(data[:PREFIX.size])
    if representation not in (0, 1) or compression not in (0, 1) or not 0 < size <= MAX_MANIFEST_BYTES:
        raise ArchiveFormatError("Unsupported or excessive wire metadata")
    try:
        raw = get_codec("zstd" if compression else "stored").decode(
            data[PREFIX.size:], {}, {}, size,
        )
    except CodecError:
        raise ArchiveFormatError("Invalid compressed metadata") from None
    if representation == 1:
        return decode_compact(raw, payload_size=payload_size)
    return parse_manifest(raw, payload_size=payload_size, version=4)
