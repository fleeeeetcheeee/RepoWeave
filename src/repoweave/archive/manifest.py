"""The bounded, portable RPW V1 header and manifest contract."""

import json
import re
import struct
import unicodedata
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from repoweave.codecs.base import MAX_ENCODED_FILE_BYTES, MAX_FILE_BYTES, validate_options

MAGIC = b"RPWEAVE\0"
VERSION = 1
HEADER = struct.Struct(">8sHQ")
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 100_000
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_PAYLOAD_BYTES = 2 * MAX_TOTAL_BYTES


class ArchiveFormatError(ValueError):
    """An unsupported or malformed archive header or manifest."""


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    path: str

    @field_validator("path")
    @classmethod
    def portable_path(cls, path: str) -> str:
        parts = path.split("/")
        if (
            not path or len(path.encode("utf-8")) > 1024 or len(parts) > 64
            or unicodedata.normalize("NFC", path) != path
            or any(ord(char) < 32 or ord(char) == 127 or char in '\\:<>"|?*' for char in path)
        ):
            raise ValueError("path must be a portable NFC relative path")
        for part in parts:
            if (
                part in ("", ".", "..") or part.endswith((".", " "))
                or len(part.encode("utf-8")) > 255
                or re.fullmatch(
                    r"CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]",
                    part.split(".")[0], re.IGNORECASE,
                )
            ):
                raise ValueError("path contains an unsafe component")
        return path


class DirectoryEntry(Entry):
    kind: Literal["directory"]


class FileEntry(Entry):
    kind: Literal["file"]
    size: int = Field(ge=0, le=MAX_FILE_BYTES)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    executable_bits: int = Field(ge=0, le=0o111)
    codec: Literal["stored", "zstd", "lzma"]
    parameters: dict[str, int]
    offset: int = Field(ge=0, le=MAX_PAYLOAD_BYTES)
    length: int = Field(ge=0, le=MAX_ENCODED_FILE_BYTES)

    @model_validator(mode="after")
    def codec_metadata(self) -> Self:
        if self.executable_bits & ~0o111:
            raise ValueError("only executable permission bits are supported")
        validate_options(self.codec, self.parameters, {})
        if self.codec == "stored" and self.length != self.size:
            raise ValueError("stored payload length must equal file size")
        return self


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    entries: list[Annotated[FileEntry | DirectoryEntry, Field(discriminator="kind")]] = Field(
        max_length=MAX_ENTRIES
    )

    @model_validator(mode="after")
    def layout(self) -> Self:
        paths: dict[str, str] = {}
        destinations: set[str] = set()
        previous = ""
        offset = 0
        total = 0
        for entry in self.entries:
            if entry.path <= previous:
                raise ValueError("entries must have unique paths in sorted order")
            previous = entry.path
            destination = unicodedata.normalize("NFC", entry.path.casefold())
            if destination in destinations:
                raise ValueError("colliding destination paths")
            destinations.add(destination)
            parent = entry.path.rpartition("/")[0]
            if parent and paths.get(parent) != "directory":
                raise ValueError("each parent must be an explicit directory")
            paths[entry.path] = entry.kind
            if isinstance(entry, FileEntry):
                if entry.offset != offset:
                    raise ValueError("payloads must be contiguous in entry order")
                offset += entry.length
                total += entry.size
        if offset > MAX_PAYLOAD_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError("archive exceeds aggregate size limits")
        return self

    @property
    def payload_size(self) -> int:
        return sum(entry.length for entry in self.entries if isinstance(entry, FileEntry))


def encode_manifest(manifest: Manifest) -> bytes:
    """Validate and serialize using the V1 canonical JSON representation."""
    try:
        validated = Manifest.model_validate(manifest.model_dump())
        data = json.dumps(
            validated.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (ValidationError, UnicodeError, ValueError, RecursionError):
        raise ArchiveFormatError("Invalid archive manifest") from None
    if len(data) > MAX_MANIFEST_BYTES:
        raise ArchiveFormatError("Manifest exceeds byte limit")
    return data


def parse_manifest(data: bytes, *, payload_size: int) -> Manifest:
    """Validate manifest bytes against the actual remaining archive length."""
    if not 0 < len(data) <= MAX_MANIFEST_BYTES:
        raise ArchiveFormatError("Invalid manifest length")
    if type(payload_size) is not int or not 0 <= payload_size <= MAX_PAYLOAD_BYTES:
        raise ArchiveFormatError("Invalid payload length")
    try:
        manifest = Manifest.model_validate(json.loads(data.decode("utf-8")))
        canonical = encode_manifest(manifest)
    except (ValidationError, UnicodeError, ValueError, RecursionError):
        raise ArchiveFormatError("Invalid archive manifest") from None
    # Comparing bytes also rejects duplicate JSON keys and alternate number spellings.
    if canonical != data:
        raise ArchiveFormatError("Manifest is not canonical JSON")
    if manifest.payload_size != payload_size:
        raise ArchiveFormatError("Payload length does not match manifest")
    return manifest


def parse_header(data: bytes) -> int:
    """Return the bounded manifest length before a reader allocates its buffer."""
    if len(data) != HEADER.size:
        raise ArchiveFormatError("Invalid archive header length")
    magic, version, manifest_length = HEADER.unpack(data)
    if magic != MAGIC:
        raise ArchiveFormatError("Invalid archive magic")
    if version != VERSION:
        raise ArchiveFormatError("Unsupported archive version")
    if not 0 < manifest_length <= MAX_MANIFEST_BYTES:
        raise ArchiveFormatError("Invalid manifest length")
    return manifest_length
