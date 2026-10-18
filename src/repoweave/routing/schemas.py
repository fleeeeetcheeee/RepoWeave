"""The fixed baseline policy needed by the pack command."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from repoweave.archive.filesystem import open_regular
from repoweave.archive.manifest import ArchiveFormatError


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    name: str = Field(default="baseline", min_length=1, max_length=100)
    codec: Literal["stored", "zstd", "lzma"] = "zstd"
    zstd_level: int = Field(default=3, ge=-7, le=22)
    dictionary_id: None = None
    grouping: Literal["none"] = "none"

    @property
    def parameters(self) -> dict[str, int]:
        return {"level": self.zstd_level} if self.codec == "zstd" else {}


def load_policy(path: Path) -> Policy:
    with open_regular(path) as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ArchiveFormatError("Policy exceeds byte limit")
    try:
        return Policy.model_validate_json(data)
    except ValidationError:
        raise ArchiveFormatError("Invalid or unsupported encoding policy") from None
