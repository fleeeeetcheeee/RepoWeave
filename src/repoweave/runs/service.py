"""One worker per run, immutable submission, checkpointed execution and cancellation."""

import asyncio
import fcntl
import re
import uuid
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from langsmith import tracing_context

from repoweave.agents import factory
from repoweave.archive.writer import write_archive
from repoweave.benchmarks.splits import Splits
from repoweave.config import Settings
from repoweave.dictionaries.train import load_bundle
from repoweave.execution.daytona import Remote
from repoweave.experiments.runner import Runner
from repoweave.experiments.schemas import engine_id, environment, file_hash
from repoweave.observability.tracing import Tracer
from repoweave.repository import create_snapshot
from repoweave.routing.objective import Limits
from repoweave.routing.policy import content_id
from repoweave.routing.schemas import Policy
from repoweave.storage.database import Store
from repoweave.workflows.improve_compression.context import Context
from repoweave.workflows.improve_compression.graph import build_graph


def submit(
    source: Path, parent: Policy, settings: Settings, *, run_id: str | None = None,
    repeats: int = 3, warmups: int = 1, approved_snapshot: str | None = None,
    limits: Limits | None = None, dictionary_ids: tuple[str, ...] = (),
    splits: Splits | None = None, repository_id: str | None = None,
) -> str:
    if not 1 <= repeats <= 20 or not 0 <= warmups <= 4:
        raise ValueError("Invalid repetition budget")
    if run_id is None:
        run_id = content_id(uuid.uuid4().hex)
    if not re.fullmatch(r"[0-9a-f]{64}", run_id):
        raise ValueError("Run ID must be a content identity")
    store = Store(settings.data_dir)
    snapshot = create_snapshot(source, store.root / "snapshots", exclude_paths=(store.root,))
    if splits is not None:
        splits.require(repository_id or "", "validation")
    if settings.execution == "daytona":
        settings.validate_daytona(dataset_id=snapshot.snapshot_id,
                                  approved_dataset_ids=[approved_snapshot] if approved_snapshot else [])
    root = store.root / "runs" / run_id
    root.mkdir(parents=True, exist_ok=True)
    with (root / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        source_archive = root / "input.rpw"
        if not source_archive.exists():
            write_archive(snapshot, source_archive, codec_id="stored")
        registered = sorted(set(dictionary_ids) | ({parent.dictionary_id} if parent.dictionary_id else set()))
        for identity in registered:
            bundle = load_bundle(settings.data_dir / "dictionaries", identity)
            if (splits is None or bundle.splits != splits
                    or repository_id in bundle.training_snapshots
                    or snapshot.snapshot_id in bundle.training_snapshots.values()):
                raise ValueError("Dictionary training provenance overlaps validation")
            bundle.save(root / "dictionaries")
        spec = {
            "snapshot_id": snapshot.snapshot_id, "input_sha256": file_hash(source_archive),
            "scope_id": snapshot.snapshot_id, "parent": parent.model_dump(), "parent_id": parent.policy_id,
            "engine_id": engine_id(), "execution": settings.execution,
            "environment_id": content_id(environment() if settings.execution == "local" else {
                "snapshot": settings.daytona_snapshot, "cpu": 2, "memory": 4, "disk": 10}),
            "daytona_snapshot": settings.daytona_snapshot if settings.execution == "daytona" else None,
            "approved_dataset_ids": [approved_snapshot] if approved_snapshot else [],
            "limits": (limits or Limits()).model_dump(), "repeats": repeats, "warmups": warmups,
            "max_candidates": min(settings.max_candidates, 3), "dictionary_ids": registered,
            "splits": splits.model_dump() if splits else None, "repository_id": repository_id,
        }
        parent.save(store.root / "policies")
        store.create_run(run_id, spec)
    return run_id


async def execute(store: Store, run_id: str, settings: Settings, *, agents=factory,
                  remote=None, tracer: Tracer | None = None) -> dict:
    row = store.run(run_id)
    root = store.root / "runs" / run_id
    with (root / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if row["status"] in ("promoted", "completed-without-improvement", "cancelled", "failed"):
            return row
        spec = row["spec"]
        settings = settings.model_copy(update={"max_candidates": spec["max_candidates"],
                                               "execution": spec["execution"],
                                               "data_dir": root})
        if spec["execution"] == "daytona" and remote is None:
            remote = Remote(store, run_id, settings)
        runner = Runner(store, run_id, settings, remote=remote)
        tracer = tracer or Tracer(settings)
        context = Context(store, settings, runner, tracer, agents)
        config = {"configurable": {"thread_id": run_id}, "callbacks": [], "recursion_limit": 30}
        try:
            runner.check_cancelled()
            await runner.prepare()
            with tracing_context(enabled=False):
                async with AsyncSqliteSaver.from_conn_string(str(store.root / "checkpoints.sqlite")) as saver:
                    graph = build_graph(saver)
                    checkpoint = await graph.aget_state(config)
                    if checkpoint.next == ("approve",) and not row["approved"]:
                        return store.run(run_id)
                    payload: Any = (Command(resume=True) if checkpoint.next == ("approve",) else
                               None if checkpoint.values else {"run_id": run_id})
                    store.status(run_id, "running")
                    async with tracer.span("workflow", run_id=run_id) as span:
                        task = asyncio.create_task(graph.ainvoke(payload, config, context=context))
                        try:
                            while not task.done():
                                await asyncio.wait({task}, timeout=0.1)
                                runner.check_cancelled()
                            await task
                        finally:
                            if not task.done():
                                task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                        span["outcome"] = store.run(run_id)["status"]
        except asyncio.CancelledError:
            store.status(run_id, "cancelled")
            raise
        except Exception:  # noqa: BLE001 - never persist exception payloads
            store.status(run_id, "failed")
            raise ValueError("Optimization failed; inspect run events") from None
        finally:
            store.event(run_id, "tracing", {"failed": tracer.failed, "span_ids": tracer.ids})
            try:
                await runner.close()
            except Exception:  # noqa: BLE001 - expose only the cleanup category
                store.event(run_id, "cleanup-pending")
                raise ValueError("Sandbox cleanup pending; run runs cleanup") from None
    return store.run(run_id)


async def approve(store: Store, run_id: str) -> None:
    with (store.root / "runs" / run_id / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tracing_context(enabled=False):
            async with AsyncSqliteSaver.from_conn_string(str(store.root / "checkpoints.sqlite")) as saver:
                checkpoint = await build_graph(saver).aget_state({"configurable": {"thread_id": run_id}})
                if checkpoint.next != ("approve",) or not any(t.interrupts for t in checkpoint.tasks):
                    raise ValueError("Run has no approval checkpoint")
                store.approve(run_id)
