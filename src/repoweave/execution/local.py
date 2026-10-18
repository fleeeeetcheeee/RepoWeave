"""Bounded subprocess execution and validation of local or downloaded results."""

import asyncio
import os
import shutil
import signal
import sys
import tempfile
from pathlib import Path

from repoweave.archive.decode import unpack
from repoweave.benchmarks.worker import _compare, _tar_decode
from repoweave.experiments.schemas import Request, Result, file_hash
from repoweave.repository import Snapshot
from repoweave.routing.policy import write_json


def stage(work: Path, request: Request, source: Path, registry: Path) -> None:
    work.mkdir(parents=True, exist_ok=True)
    if not (work / "request.json").exists():
        write_json(work / "request.json", request.model_dump())
    if not (work / "input.rpw").exists():
        os.link(source, work / "input.rpw")
    if request.policy.dictionary_id:
        from repoweave.dictionaries.train import load_bundle

        bundle = load_bundle(registry, request.policy.dictionary_id)
        bundle.save(work / "dictionaries")


async def execute(work: Path, timeout: float, *, resume: bool = False) -> None:
    if resume:
        # Never rerun an uncertain side effect after worker death.
        async with asyncio.timeout(timeout):
            while not (work / "result.json").exists():
                await asyncio.sleep(0.2)
        return
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "repoweave.execution.experiment_entrypoint", "measure", str(work),
        cwd=work, env={}, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        async with asyncio.timeout(timeout):
            if await process.wait() != 0:
                raise ValueError("Experiment process failed")
    finally:
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGKILL)
            await process.wait()


def validate_result(work: Path, request: Request, snapshot: Snapshot) -> Result:
    from repoweave.archive.filesystem import open_regular

    with open_regular(work / "result.json") as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("Experiment result exceeds byte limit")
    result = Result.model_validate_json(data)
    if (result.job_id != request.job_id or result.snapshot_id != request.snapshot_id
            or result.policy_id != request.policy.policy_id or result.engine_id != request.engine_id
            or result.environment_id != request.environment_id
            or file_hash(work / "archive.bin") != result.artifact_sha256
            or (work / "archive.bin").stat().st_size != result.sample.archive_bytes):
        raise ValueError("Experiment result identities or artifact differ")
    with tempfile.TemporaryDirectory(prefix="validate-", dir=work) as directory:
        restored = Path(directory) / "restored"
        if request.method == "rpw":
            unpack(work / "archive.bin", restored)
        else:
            _tar_decode(snapshot, work / "archive.bin", restored, request.method.removeprefix("tar-"))
        _compare(snapshot, restored)
    return result


def export_archive(source: Path, destination: Path) -> None:
    if destination.exists():
        if file_hash(source) != file_hash(destination):
            raise ValueError("Existing archive differs")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, destination.open("xb") as writer:
        shutil.copyfileobj(reader, writer)
