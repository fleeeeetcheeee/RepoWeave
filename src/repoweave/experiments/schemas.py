"""Fixed engine operations; no commands, source paths, or credentials in requests."""

import hashlib
import importlib.metadata
import platform
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from repoweave.archive.filesystem import open_regular
from repoweave.archive.manifest import MAX_PAYLOAD_BYTES
from repoweave.routing.objective import Limits
from repoweave.routing.policy import content_id
from repoweave.routing.schemas import Policy

Identity = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
MAX_ARTIFACT_BYTES = MAX_PAYLOAD_BYTES + 32 * 1024 * 1024


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True,
                             hide_input_in_errors=True, allow_inf_nan=False)


class Request(StrictModel):
    schema_version: Literal[1] = 1
    snapshot_id: Identity
    input_sha256: Identity
    policy: Policy
    method: Literal["rpw", "tar-zstd", "tar-lzma"] = "rpw"
    environment_id: Identity
    engine_id: Identity
    repetition: int = Field(ge=0, le=24)
    limits: Limits

    @property
    def job_id(self) -> str:
        return content_id(self.model_dump())


class Sample(StrictModel):
    archive_bytes: int = Field(ge=0, le=MAX_ARTIFACT_BYTES)
    reconstructed: Literal[True]
    encode_seconds: float = Field(ge=0)
    decode_seconds: float = Field(ge=0)
    encode_peak_rss_bytes: int = Field(ge=0)
    decode_peak_rss_bytes: int = Field(ge=0)
    manifest_bytes: int | None = Field(default=None, ge=0)
    payload_bytes: int | None = Field(default=None, ge=0)
    embedded_dictionary_bytes: int = Field(default=0, ge=0)


class Result(StrictModel):
    schema_version: Literal[1] = 1
    job_id: Identity
    snapshot_id: Identity
    policy_id: Identity
    engine_id: Identity
    environment_id: Identity
    artifact_sha256: Identity
    sample: Sample
    environment: dict[str, str | int]
    memory_method: Literal["process-lifetime peak RSS; macOS bytes, Linux KiB converted to bytes"] = (
        "process-lifetime peak RSS; macOS bytes, Linux KiB converted to bytes"
    )


def file_hash(path: Path, limit: int = MAX_ARTIFACT_BYTES) -> str:
    digest = hashlib.sha256()
    size = 0
    with open_regular(path) as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise ValueError("Artifact exceeds byte limit")
            digest.update(chunk)
    return digest.hexdigest()


def engine_sources(root: Path) -> list[Path]:
    """The same allowlist defines the engine revision and the hosted wheel."""
    paths = [path for folder in ("archive", "codecs", "repository", "repository_graph",
                                 "routing", "dictionaries", "benchmarks")
             for path in (root / folder).rglob("*.py")]
    paths += [root / name for name in ("__init__.py", "experiments/__init__.py",
                                      "experiments/schemas.py", "execution/__init__.py",
                                      "execution/experiment_entrypoint.py")]
    return sorted(paths)


def engine_id() -> str:
    root = Path(__file__).resolve().parents[1]
    return content_id({"files": {str(path.relative_to(root)): file_hash(path)
                                for path in engine_sources(root)},
                       "versions": {name: importlib.metadata.version(name)
                                    for name in ("pydantic", "zstandard")}})


def environment() -> dict[str, str | int]:
    import os

    import zstandard

    return {"python": platform.python_version(), "platform": platform.platform(),
            "machine": platform.machine(), "cpu_count": os.cpu_count() or 1,
            "zstandard": zstandard.__version__,
            "libzstd": ".".join(map(str, zstandard.ZSTD_VERSION)),
            "pydantic": importlib.metadata.version("pydantic")}
