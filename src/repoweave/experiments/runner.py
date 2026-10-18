"""Persist experiments before dispatch and reuse only revalidated completed jobs."""

import asyncio
from pathlib import Path

from repoweave.archive.decode import unpack
from repoweave.config import Settings
from repoweave.dictionaries.train import load_bundle
from repoweave.execution import local
from repoweave.experiments.schemas import Request, Result, engine_id, environment, file_hash
from repoweave.repository import create_snapshot
from repoweave.routing.objective import Limits, score
from repoweave.routing.policy import content_id
from repoweave.routing.schemas import Policy
from repoweave.storage.database import Store


class Runner:
    def __init__(self, store: Store, run_id: str, settings: Settings, remote=None):
        self.store, self.run_id, self.settings, self.remote = store, run_id, settings, remote
        self.spec = store.run(run_id)["spec"]
        self.root = store.root / "runs" / run_id
        self.source = self.root / "input.rpw"
        self.registry = self.root / "dictionaries"
        self.snapshot = None

    def check_cancelled(self) -> None:
        if self.store.run(self.run_id)["cancel"]:
            raise asyncio.CancelledError

    async def prepare(self) -> None:
        if engine_id() != self.spec["engine_id"]:
            raise ValueError("Engine changed; start a new run")
        if self.remote is None and content_id(environment()) != self.spec["environment_id"]:
            raise ValueError("Execution environment changed; start a new run")
        if not (self.root / "input").exists():
            unpack(self.source, self.root / "input")
        self.snapshot = create_snapshot(self.root / "input", self.root / "captures")
        if self.snapshot.snapshot_id != self.spec["snapshot_id"]:
            raise ValueError("Persisted input snapshot changed")

    def request(self, policy: Policy, method: str, repetition: int) -> Request:
        return Request.model_validate({
            "snapshot_id": self.spec["snapshot_id"], "input_sha256": self.spec["input_sha256"],
            "policy": policy.model_dump(), "method": method, "repetition": repetition,
            "environment_id": self.spec["environment_id"], "engine_id": self.spec["engine_id"],
            "limits": self.spec["limits"],
        })

    async def job(self, request: Request) -> Result:
        self.check_cancelled()
        if self.snapshot is None:
            await self.prepare()
        assert self.snapshot is not None
        work = self.root / "jobs" / request.job_id
        local.stage(work, request, self.source, self.registry)
        existing = self.store.job(self.run_id, request.job_id)
        if existing and existing["status"] == "complete":
            result = local.validate_result(work, request, self.snapshot)
            if result.model_dump() != self.store.read_artifact(existing["result_id"]):
                raise ValueError("Persisted experiment differs")
            return result
        fresh = self.store.claim_job(self.run_id, request.job_id)
        self.store.event(self.run_id, "experiment", {"job_id": request.job_id})
        if not (work / "result.json").exists():
            timeout = request.limits.encode_seconds + request.limits.decode_seconds + 60
            if self.remote is None:
                await local.execute(work, timeout, resume=not fresh)
            else:
                await self.remote.execute(work, request, resume=not fresh)
        result = local.validate_result(work, request, self.snapshot)
        previous_environment = self.store.phase(self.run_id, "environment")
        if previous_environment is not None:
            if self.store.read_artifact(previous_environment) != result.environment:
                raise ValueError("Experiment machine/environment differs from baseline")
        else:
            self.store.phase(self.run_id, "environment", result.environment)
        self.store.finish_job(self.run_id, request.job_id, result.model_dump())
        return result

    async def report(self, policy: Policy) -> dict:
        self.check_cancelled()
        configurations = [("candidate", "rpw", policy), ("rpw-zstd", "rpw", Policy()),
                          ("tar-zstd", "tar-zstd", Policy()), ("tar-lzma", "tar-lzma", Policy())]
        control = None
        if policy.grouping == "graph":
            bundle = load_bundle(self.registry, policy.dictionary_id or "")
            for identity in self.spec["dictionary_ids"]:
                other = load_bundle(self.registry, identity)
                if other.grouping == "extension" and other.training_snapshots == bundle.training_snapshots:
                    control = Policy(zstd_level=policy.zstd_level, dictionary_id=identity, grouping="extension")
                    break
            if control is None:
                raise ValueError("Graph grouping requires a matching extension control")
            configurations.insert(1, ("extension-control", "rpw", control))
        results = []
        limits = Limits.model_validate(self.spec["limits"])
        for name, method, selected in configurations:
            samples = []
            jobs = []
            for index in range(self.spec["warmups"] + self.spec["repeats"]):
                result = await self.job(self.request(selected, method, index))
                if index >= self.spec["warmups"]:
                    samples.append(result.sample.model_dump())
                    jobs.append(result.job_id)
            item = {"name": name, "method": method, "status": "ok", "samples": samples,
                    "policy_id": selected.policy_id if method == "rpw" else None, "job_ids": jobs}
            item["score_bytes"] = score(item, limits)
            item["eligible"] = item["score_bytes"] is not None
            results.append(item)
        assert self.snapshot is not None
        return {"schema_version": 1, "snapshot": self.snapshot.to_report(),
                "original_bytes": sum(entry.size_bytes or 0 for entry in self.snapshot.entries),
                "policy": policy.model_dump(), "policy_id": policy.policy_id,
                "limits": limits.model_dump(), "partition": "validation", "final": False,
                "repeats": self.spec["repeats"], "warmups": self.spec["warmups"], "results": results,
                "control_policy": control.model_dump() if control else None,
                "environment": self.store.read_artifact(self.store.phase(self.run_id, "environment")),
                "measurement": "Sequential fresh child processes; warmups excluded; process-lifetime RSS"}

    async def independent_decode(self, report: dict) -> None:
        identity = report["results"][0]["job_ids"][0]
        archive = self.root / "jobs" / identity / "archive.bin"
        local.export_archive(archive, self.root / "selected.rpw")
        if self.remote is not None:
            await self.remote.finish_experiments()
            await self.remote.independent_decode(archive, self.spec["snapshot_id"])

    async def close(self) -> None:
        if self.remote is not None:
            await self.remote.close()
