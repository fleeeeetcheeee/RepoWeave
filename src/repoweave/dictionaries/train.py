"""Train bounded dictionary groups exclusively from designated training snapshots."""

import base64
import hashlib
import json
from pathlib import Path
from typing import Literal

import zstandard
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from repoweave.archive.filesystem import open_regular
from repoweave.archive.integrity import check_bytes
from repoweave.archive.manifest import MAX_MANIFEST_BYTES, ArchiveFormatError
from repoweave.benchmarks.splits import Splits
from repoweave.codecs.base import MAX_DICTIONARY_BYTES
from repoweave.repository import Snapshot
from repoweave.repository_graph import build_graph, group_files
from repoweave.routing.policy import canonical, content_id, write_json

MAX_TRAINING_BYTES = 64 * 1024 * 1024


class Sample(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    repository_id: str
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DictionaryGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    samples: list[Sample]
    status: Literal["trained", "training-failed"]
    data: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    def decoded(self) -> bytes | None:
        if self.status == "training-failed":
            if self.data is not None or self.sha256 is not None:
                raise ValueError("Failed training cannot have an asset")
            return None
        if self.data is None or len(self.data) > 4 * ((MAX_DICTIONARY_BYTES + 2) // 3):
            raise ValueError("Missing or excessive dictionary asset")
        data = base64.b64decode(self.data, validate=True)
        if (not 0 < len(data) <= MAX_DICTIONARY_BYTES
                or hashlib.sha256(data).hexdigest() != self.sha256):
            raise ValueError("Invalid dictionary hash")
        return data


class DictionaryBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: int = Field(default=1, ge=1, le=1)
    grouping: Literal["none", "extension", "graph"]
    dictionary_size: int = Field(ge=256, le=MAX_DICTIONARY_BYTES)
    zstandard_version: str
    training_parameters: Literal["zstd.train_dictionary defaults; threads=0"] = (
        "zstd.train_dictionary defaults; threads=0"
    )
    splits: Splits
    training_snapshots: dict[str, str]
    groups: dict[str, DictionaryGroup] = Field(max_length=64)

    @property
    def dictionary_id(self) -> str:
        return content_id(self.model_dump())

    def validate_assets(self) -> None:
        for repository_id, snapshot_id in self.training_snapshots.items():
            self.splits.require(repository_id, "train")
            if len(snapshot_id) != 64 or any(char not in "0123456789abcdef" for char in snapshot_id):
                raise ValueError("Invalid training snapshot identity")
        for group in self.groups.values():
            group.decoded()
            for sample in group.samples:
                self.splits.require(sample.repository_id, "train")
                if self.training_snapshots.get(sample.repository_id) != sample.snapshot_id:
                    raise ValueError("Training sample provenance differs from repository identity")

    def for_snapshot(self, snapshot: Snapshot) -> tuple[dict[str, bytes], list[str]]:
        self.validate_assets()
        result: dict[str, bytes] = {}
        fallbacks: list[str] = []
        for key, paths in group_files(build_graph(snapshot), self.grouping).items():
            group = self.groups.get(key)
            data = group.decoded() if group else None
            if data is None:
                fallbacks.append(key)
            else:
                result.update({path: data for path in paths})
        return result, fallbacks

    def save(self, directory: Path) -> Path:
        self.validate_assets()
        if len(canonical(self.model_dump())) + 1 > MAX_MANIFEST_BYTES:
            raise ValueError("Dictionary bundle exceeds byte limit")
        output = directory / f"{self.dictionary_id}.json"
        if output.exists():
            load_bundle(directory, self.dictionary_id)
        else:
            write_json(output, self.model_dump())
        return output


def load_bundle(directory: Path, dictionary_id: str) -> DictionaryBundle:
    if len(dictionary_id) != 64 or any(char not in "0123456789abcdef" for char in dictionary_id):
        raise ArchiveFormatError("Invalid dictionary identity")
    try:
        with open_regular(directory / f"{dictionary_id}.json") as stream:
            data = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(data) > MAX_MANIFEST_BYTES:
            raise ValueError("Dictionary bundle exceeds byte limit")
        bundle = DictionaryBundle.model_validate(json.loads(data))
        bundle.validate_assets()
        if bundle.dictionary_id != dictionary_id:
            raise ValueError("Dictionary bundle identity mismatch")
        return bundle
    except (OSError, ValueError, ValidationError):
        raise ArchiveFormatError("Missing or invalid dictionary bundle") from None


def train_dictionaries(
    snapshots: dict[str, Snapshot], splits: Splits, *, grouping: Literal["none", "extension", "graph"],
    dictionary_size: int = 8192,
) -> DictionaryBundle:
    if not 256 <= dictionary_size <= MAX_DICTIONARY_BYTES:
        raise ValueError("Unsupported dictionary size")
    if not snapshots or len({snapshot.snapshot_id for snapshot in snapshots.values()}) != len(snapshots):
        raise ValueError("Provide distinct training repositories")
    samples: dict[str, list[bytes]] = {}
    identities: dict[str, list[Sample]] = {}
    total = 0
    for repository_id, snapshot in sorted(snapshots.items()):
        splits.require(repository_id, "train")
        by_path = {entry.path: entry for entry in snapshot.entries}
        for key, paths in group_files(build_graph(snapshot), grouping).items():
            for path in paths:
                entry = by_path[path]
                if not entry.size_bytes:
                    continue
                total += entry.size_bytes
                if total > MAX_TRAINING_BYTES:
                    raise ValueError("Training samples exceed the 64 MiB limit")
                with open_regular(snapshot.files_root / path) as stream:
                    data = stream.read(entry.size_bytes + 1)
                assert entry.sha256 is not None
                check_bytes(data, entry.size_bytes, entry.sha256)
                samples.setdefault(key, []).append(data)
                identities.setdefault(key, []).append(Sample(
                    repository_id=repository_id, snapshot_id=snapshot.snapshot_id,
                    path=path, sha256=entry.sha256,
                ))
    if len(samples) > 64:
        raise ValueError("Training has more than 64 dictionary groups")
    groups = {}
    for key, data_samples in sorted(samples.items()):
        try:
            data = zstandard.train_dictionary(dictionary_size, list(data_samples), threads=0).as_bytes()
        except zstandard.ZstdError:
            groups[key] = DictionaryGroup(samples=identities[key], status="training-failed")
        else:
            groups[key] = DictionaryGroup(
                samples=identities[key], status="trained", data=base64.b64encode(data).decode("ascii"),
                sha256=hashlib.sha256(data).hexdigest(),
            )
    return DictionaryBundle(grouping=grouping, dictionary_size=dictionary_size,
                            zstandard_version=zstandard.__version__, splits=splits, groups=groups,
                            training_snapshots={key: snapshot.snapshot_id
                                                for key, snapshot in sorted(snapshots.items())})
