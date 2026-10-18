"""Browser inputs contain fixture and content IDs, never server paths or commands."""

from typing import Literal

from pydantic import Field, model_validator

from repoweave.benchmarks.splits import Splits
from repoweave.experiments.schemas import Identity, StrictModel
from repoweave.routing.objective import Limits
from repoweave.routing.schemas import Policy


class Registration(StrictModel):
    fixture: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")


class Submission(StrictModel):
    repository_id: Identity
    operation: Literal["pack", "benchmark", "optimize"]
    policy: Policy = Field(default_factory=Policy)
    execution: Literal["local", "daytona"] = "local"
    approved_snapshot: Identity | None = None
    repeats: int = Field(default=3, ge=1, le=20)
    warmups: int = Field(default=1, ge=0, le=4)
    limits: Limits = Field(default_factory=Limits)
    dictionary_ids: list[Identity] = Field(default_factory=list, max_length=64)
    splits: Splits | None = None
    partition_repository_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def supported(self):
        if self.operation == "benchmark" and self.repeats < 2:
            raise ValueError("Benchmarks require at least two measured repetitions")
        if self.execution == "daytona" and self.operation != "optimize":
            raise ValueError("Cloud execution requires optimize")
        if self.execution == "daytona" and self.approved_snapshot != self.repository_id:
            raise ValueError("Explicit snapshot approval is required")
        if (self.splits is None) != (self.partition_repository_id is None):
            raise ValueError("Partitions and repository identity must be supplied together")
        return self
