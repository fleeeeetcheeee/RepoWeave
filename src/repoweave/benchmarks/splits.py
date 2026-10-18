"""Explicit whole-repository partitions; never split files between datasets."""

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from repoweave.archive.filesystem import open_regular
from repoweave.archive.manifest import ArchiveFormatError


class Splits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    train: list[str]
    validation: list[str]
    test: list[str]
    test_retired: bool = False

    @model_validator(mode="after")
    def disjoint(self) -> Self:
        ids = self.train + self.validation + self.test
        if len(ids) != len(set(ids)) or any(not item or len(item) > 200 for item in ids):
            raise ValueError("Repository IDs must be unique across all partitions")
        return self

    def require(self, repository_id: str, partition: Literal["train", "validation", "test"]) -> None:
        if repository_id not in getattr(self, partition):
            raise ValueError("Repository is not in the requested partition")
        if partition == "test" and self.test_retired:
            raise ValueError("Retired test partition cannot be used as a final holdout")


def load_splits(path: Path) -> Splits:
    with open_regular(path) as stream:
        data = stream.read(1024 * 1024 + 1)
    try:
        if len(data) > 1024 * 1024:
            raise ValueError("Split file too large")
        return Splits.model_validate_json(data)
    except ValueError:
        raise ArchiveFormatError("Invalid repository dataset partitions") from None
