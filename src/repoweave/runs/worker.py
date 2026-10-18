"""Single durable worker: python -m repoweave.runs.worker [--once]."""

import argparse
import asyncio
import fcntl
import json
import os
import signal
import sys
import tempfile
from pathlib import Path

from repoweave.api.schemas import Submission
from repoweave.archive.decode import unpack
from repoweave.archive.integrity import verify
from repoweave.config import Settings, load_settings
from repoweave.experiments.schemas import file_hash
from repoweave.repository import create_snapshot
from repoweave.routing.policy import write_json
from repoweave.routing.schemas import Policy
from repoweave.runs import service
from repoweave.runs.queue import Queue
from repoweave.storage.database import Store


async def command(queue: Queue, identity: str, args: list[str], cwd: Path, timeout: float) -> None:
    # Empty child cwd/env prevents loading the backend .env; output stays out of API events.
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "repoweave.cli", *args, cwd=cwd,
        env={"PATH": os.defpath, "REPOWEAVE_DATA_DIR": str(cwd / "engine-data")},
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        async with asyncio.timeout(timeout):
            while proc.returncode is None:
                if queue.get(identity)["cancel"]:
                    raise asyncio.CancelledError
                try:
                    await asyncio.wait_for(proc.wait(), 0.1)
                except TimeoutError:
                    pass
            if proc.returncode != 0:
                raise ValueError("Engine command failed")
    finally:
        # Kill the process group too: benchmark may have an active measurement child.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), 2)
        except TimeoutError:
            pass
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()


async def process(queue: Queue, row: dict, settings: Settings, *, agents=None) -> None:
    identity, store = row["id"], queue.store
    request = Submission.model_validate(row["request"])
    source = store.root / queue.repository(request.repository_id)["source"]
    if queue.get(identity)["cancel"]:
        raise asyncio.CancelledError
    snapshot = await asyncio.to_thread(create_snapshot, source, store.root / "worker-captures")
    if snapshot.snapshot_id != request.repository_id:
        raise ValueError("Registered snapshot changed")
    bundles = service.registered_bundles(
        store.root / "dictionaries", request.policy, tuple(request.dictionary_ids),
        request.repository_id, request.splits, request.partition_repository_id,
    )
    if request.operation == "optimize":
        selected = settings.model_copy(update={"execution": request.execution})
        service.submit(source, request.policy, selected, run_id=identity,
                       repeats=request.repeats, warmups=request.warmups,
                       approved_snapshot=request.approved_snapshot, limits=request.limits,
                       dictionary_ids=tuple(request.dictionary_ids), splits=request.splits,
                       repository_id=request.partition_repository_id)
        if queue.get(identity)["cancel"]:
            store.cancel(identity)
        kwargs = {"agents": agents} if agents is not None else {}
        result = await service.execute(store, identity, selected, **kwargs)
        selected_archive = store.root / "runs" / identity / "selected.rpw"
        if selected_archive.exists():
            queue.publish(identity, selected_archive, "archive")
        for phase in ("baseline", "decision", "summary"):
            artifact = store.phase(identity, phase)
            if artifact:
                store.read_artifact(artifact)
                queue.publish(identity, store.root / "artifacts" / f"{artifact}.json", phase)
        queue.finish(identity, result["status"])
    else:
        root = store.root / "api-work" / identity
        root.mkdir(parents=True, exist_ok=True)
        # An interrupted attempt is retried in a fresh directory, never trusted as complete.
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            work = Path(temporary)
            policy_path = request.policy.save(work / "policies")
            for bundle in bundles:
                bundle.save(work / "engine-data" / "dictionaries")
            output = work / ("archive.rpw" if request.operation == "pack" else "report.json")
            args = [request.operation, str(source), "--policy", str(policy_path), "--output", str(output)]
            if request.operation == "benchmark":
                args += ["--repeats", str(request.repeats), "--warmups", str(request.warmups),
                         "--max-encode-seconds", str(request.limits.encode_seconds),
                         "--max-decode-seconds", str(request.limits.decode_seconds),
                         "--max-memory-bytes", str(request.limits.peak_memory_bytes)]
                if request.splits is not None:
                    write_json(work / "splits.json", request.splits.model_dump())
                    args += ["--splits", str(work / "splits.json"), "--repository-id",
                             request.partition_repository_id or ""]
                if request.policy.grouping == "graph":
                    selected_bundle = next(b for b in bundles if b.dictionary_id == request.policy.dictionary_id)
                    control_bundle = next((b for b in bundles if b.grouping == "extension"
                                           and b.training_snapshots == selected_bundle.training_snapshots), None)
                    if control_bundle is None:
                        raise ValueError("Graph benchmarks require a matching extension control")
                    control = Policy(dictionary_id=control_bundle.dictionary_id, grouping="extension",
                                     zstd_level=request.policy.zstd_level)
                    args += ["--control-policy", str(control.save(work / "policies"))]
            timeout = (request.limits.encode_seconds + request.limits.decode_seconds + 30) * (
                4 * (request.repeats + request.warmups) if request.operation == "benchmark" else 1)
            await command(queue, identity, args, work, timeout)
            if request.operation == "pack":
                await asyncio.to_thread(unpack, output, work / "restored")
                await asyncio.to_thread(verify, source, work / "restored")
            else:
                report = json.loads(output.read_bytes())
                if report["snapshot"]["snapshot_id"] != request.repository_id:
                    raise ValueError("Benchmark snapshot differs")
            final = root / (file_hash(output) + output.suffix)
            output.replace(final)
            queue.publish(identity, final, "archive" if request.operation == "pack" else "benchmark")
        queue.finish(identity, "completed")


async def run_once(settings: Settings, *, agents=None) -> bool:
    store = Store(settings.data_dir)
    queue = Queue(store)
    with (store.root / "api-worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        row = queue.take()
        if row is None:
            return False
        try:
            await process(queue, row, settings, agents=agents)
        except asyncio.CancelledError:
            cancelled = queue.get(row["id"])["cancel"]
            queue.finish(row["id"], "cancelled" if cancelled else "interrupted")
            if not cancelled:
                raise
        except Exception as error:  # noqa: BLE001 - never persist exception payloads
            store.event(row["id"], "worker-failed", {"error_type": type(error).__name__})
            queue.finish(row["id"], "failed")
        return True


async def serve(settings: Settings, *, once: bool = False) -> None:
    task = asyncio.current_task()
    assert task is not None
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    while True:
        worked = await run_once(settings)
        if once:
            return
        if not worked:
            await asyncio.sleep(0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(serve(load_settings(), once=args.once))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except BlockingIOError:
        parser.exit(2, "Another worker is active\n")


if __name__ == "__main__":
    main()
