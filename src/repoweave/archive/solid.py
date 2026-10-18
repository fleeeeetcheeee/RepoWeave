"""Bounded, independent multi-file frames; no policy registry or external assets."""

import json
import os
from typing import BinaryIO, Literal

from repoweave.codecs.registry import get_codec
from repoweave.repository import Snapshot

from .filesystem import open_regular
from .integrity import check_bytes
from .manifest import ArchiveFormatError, Block, DirectoryEntry, FileEntry, Manifest


def build_solid(
    snapshot: Snapshot, original: Manifest, parameters: dict[str, int],
    target: int, payloads: BinaryIO,
    *, codec_id: str = "zstd",
) -> tuple[Manifest, list[Block]]:
    """Append candidate payloads to the spool; retain at most one decoded block."""
    entries: list[FileEntry | DirectoryEntry] = []
    blocks: list[Block] = []
    spooled: list[Block] = []
    buffer = bytearray()
    file_count = 0
    offset = 0

    def flush() -> None:
        nonlocal file_count, offset
        if not file_count:
            return
        data = bytes(buffer)
        buffer.clear()
        selected_codec: Literal["zstd", "lzma"] = "lzma" if codec_id == "lzma" else "zstd"
        compressed = get_codec(selected_codec).encode(data, parameters, {})
        stored = Block(codec="stored", parameters={}, size=len(data),
                       offset=offset, length=len(data))
        candidate = Block(codec=selected_codec, parameters=parameters, size=len(data),
                          offset=offset, length=len(compressed))
        def cost(block: Block) -> int:
            return block.length + len(json.dumps(block.model_dump(), separators=(",", ":")))
        selected, payload = (candidate, compressed) if cost(candidate) < cost(stored) else (stored, data)
        blocks.append(selected)
        spooled.append(selected.model_copy(update={"offset": payloads.tell()}))
        payloads.write(payload)
        offset += selected.length
        file_count = 0

    for entry in original.entries:
        if isinstance(entry, DirectoryEntry):
            entries.append(entry)
            continue
        if file_count and len(buffer) + entry.size > target:
            flush()
        with open_regular(snapshot.files_root / entry.path) as source:
            data = source.read(entry.size + 1)
            if os.fstat(source.fileno()).st_mode & 0o111 != entry.executable_bits:
                raise ArchiveFormatError("Snapshot executable bits changed")
        check_bytes(data, entry.size, entry.sha256)
        entries.append(entry.model_copy(update={
            "codec": "solid", "block_index": len(blocks), "offset": len(buffer),
            "length": entry.size, "parameters": {},
        }))
        buffer.extend(data)
        del data
        file_count += 1
        if len(buffer) >= target:
            flush()
    flush()
    return Manifest(entries=entries, blocks=blocks), spooled
