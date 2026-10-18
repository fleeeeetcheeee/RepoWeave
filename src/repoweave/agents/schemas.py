"""Strict, bounded data crossing the agent boundary."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from repoweave.routing.objective import Limits
from repoweave.routing.policy import content_id
from repoweave.routing.schemas import Policy

ContentId = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
EvidenceIds = Annotated[list[ContentId], Field(min_length=1, max_length=20)]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, hide_input_in_errors=True,
        allow_inf_nan=False, revalidate_instances="always",
    )


class MeasurementSample(StrictModel):
    archive_bytes: int = Field(ge=0)
    reconstructed: bool
    encode_seconds: float = Field(ge=0)
    decode_seconds: float = Field(ge=0)
    encode_peak_rss_bytes: int = Field(ge=0)
    decode_peak_rss_bytes: int = Field(ge=0)
    manifest_bytes: int | None = Field(default=None, ge=0)
    payload_bytes: int | None = Field(default=None, ge=0)
    embedded_dictionary_bytes: int = Field(default=0, ge=0)


class Measurement(StrictModel):
    result_id: ContentId
    method: Literal["rpw", "tar-zstd", "tar-lzma"]
    policy_id: ContentId | None
    status: Literal["ok", "failed"]
    samples: list[MeasurementSample] = Field(max_length=20)


class Evidence(StrictModel):
    snapshot_id: ContentId
    original_bytes: int = Field(ge=0)
    limits: Limits
    results: list[Measurement] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_results(self) -> Self:
        if len({result.result_id for result in self.results}) != len(self.results):
            raise ValueError("Duplicate result identities")
        return self

    @property
    def result_ids(self) -> set[str]:
        return {result.result_id for result in self.results}


class Hypothesis(StrictModel):
    explanation: str = Field(min_length=1, max_length=2000)
    uncertainty: Literal["low", "medium", "high"]
    evidence_ids: EvidenceIds


class Diagnosis(StrictModel):
    summary: str = Field(min_length=1, max_length=4000)
    evidence_ids: EvidenceIds
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=5)


class CandidatePolicy(Policy):
    codec: Literal["zstd", "lzma"] = "zstd"


class Candidate(StrictModel):
    parent_policy_id: ContentId
    policy: CandidatePolicy
    rationale: str = Field(min_length=1, max_length=2000)
    evidence_ids: EvidenceIds


class Proposals(StrictModel):
    candidates: list[Candidate] = Field(min_length=1, max_length=3)


def summarize_benchmark(report: dict) -> Evidence:
    """Project a local report onto safe fields; never send paths, names, or errors."""
    try:
        if report["final"] or report["partition"] == "test":
            raise ValueError("Final holdout results cannot inform agent search")
        if not 1 <= len(report["results"]) <= 20:
            raise ValueError("Invalid result count")
        results = []
        for index, result in enumerate(report["results"]):
            if len(result["samples"]) > 20:
                raise ValueError("Too many samples")
            samples = [MeasurementSample.model_validate({
                key: sample[key] for key in MeasurementSample.model_fields if key in sample
            }) for sample in result["samples"]]
            values = {
                "method": result["method"], "policy_id": result["policy_id"],
                "status": result["status"], "samples": [sample.model_dump() for sample in samples],
            }
            result_id = content_id({"snapshot_id": report["snapshot"]["snapshot_id"],
                                    "index": index, **values})
            results.append(Measurement.model_validate({"result_id": result_id, **values}))
        return Evidence(snapshot_id=report["snapshot"]["snapshot_id"],
                        original_bytes=report["original_bytes"],
                        limits=Limits.model_validate(report["limits"]), results=results)
    except (KeyError, TypeError, ValueError):
        raise ValueError("Invalid benchmark evidence or final holdout report") from None
