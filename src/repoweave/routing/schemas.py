"""Validated, immutable encoding policies and their content identities."""

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from repoweave.archive.filesystem import open_regular
from repoweave.archive.manifest import ArchiveFormatError

from .policy import content_id, write_json


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    name: str = Field(default="baseline", min_length=1, max_length=100)
    codec: Literal["stored", "zstd", "lzma"] = "zstd"
    zstd_level: int = Field(default=3, ge=-7, le=22)
    dictionary_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    grouping: Literal["none", "extension", "graph"] = "none"

    @model_validator(mode="after")
    def dictionary_codec(self) -> Self:
        if self.dictionary_id is not None and self.codec != "zstd":
            raise ValueError("Dictionary policies require Zstd")
        if self.dictionary_id is None and self.grouping != "none":
            raise ValueError("Grouping requires a dictionary bundle")
        return self

    @property
    def policy_id(self) -> str:
        return content_id(self.model_dump())

    def save(self, directory: Path) -> Path:
        output = directory / f"{self.policy_id}.json"
        if output.exists():
            if load_policy(output) != self:
                raise ArchiveFormatError("Policy identity collision")
        else:
            write_json(output, self.model_dump())
        return output

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
