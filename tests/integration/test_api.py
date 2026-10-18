"""Local HTTP contract with a real SQLite queue, worker, and compression engine."""

import asyncio
import fcntl
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from test_optimization import Agents

from repoweave.api.app import create_app
from repoweave.archive.decode import unpack
from repoweave.config import Settings
from repoweave.runs.queue import Queue
from repoweave.runs.worker import run_once
from repoweave.storage.database import Store


def client(settings):
    return TestClient(create_app(settings), base_url="http://127.0.0.1:8000",
                      client=("127.0.0.1", 50000))


@pytest.fixture
def setup_api(tmp_path):
    source = tmp_path / "owned-fixture"
    source.mkdir()
    (source / "sample.py").write_bytes(b"raise RuntimeError('do not execute fixture')\n" * 500)
    (source / "sample.py").chmod(0o755)
    (source / "empty").mkdir()
    settings = Settings(REPOWEAVE_DATA_DIR=tmp_path / "state", REPOWEAVE_FIXTURES={"owned": source},
                        REPOWEAVE_MAX_CANDIDATES=1)
    with client(settings) as api:
        response = api.post("/repositories", json={"fixture": "owned"})
        assert response.status_code == 201, response.text
        yield settings, api, response.json()["id"], source


def submit(api, repository_id, *, operation="pack", key="test", **kwargs):
    response = api.post("/runs", headers={"Idempotency-Key": key}, json={
        "repository_id": repository_id, "operation": operation, "repeats": 2 if operation == "benchmark" else 1, "warmups": 0, **kwargs,
    })
    assert response.status_code == 202, response.text
    return response.json()["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["pack", "benchmark"])
async def test_queue_work_artifacts_and_api_restart(setup_api, operation, tmp_path):
    settings, api, repository_id, source = setup_api
    assert api.get("/health").json() == {"status": "ready"}
    assert api.get(f"/repositories/{repository_id}/graph").status_code == 200
    identity = submit(api, repository_id, operation=operation)
    assert api.get(f"/runs/{identity}").json()["status"] == "queued"
    assert await run_once(settings)
    result = api.get(f"/runs/{identity}").json()
    assert result["status"] == "completed", result
    artifact, = result["artifacts"]
    response = api.get(f"/artifacts/{artifact['id']}")
    assert response.status_code == 200
    if operation == "pack":
        archive = tmp_path / "out.rpw"
        archive.write_bytes(response.content)
        unpack(archive, tmp_path / "restored")
        assert (tmp_path / "restored/sample.py").read_bytes() == (source / "sample.py").read_bytes()
        assert (tmp_path / "restored/sample.py").stat().st_mode & 0o111 == 0o111
    else:
        assert response.json()["results"][0]["samples"][0]["reconstructed"]
        assert result["metrics"]["snapshot"]["snapshot_id"] == repository_id
    with client(settings) as restarted:
        assert restarted.get(f"/runs/{identity}").json() == result
        assert restarted.get(f"/artifacts/{artifact['id']}").content == response.content
    assert not await run_once(settings)


def test_duplicate_and_conflicting_idempotency(setup_api):
    _, api, repository_id, _ = setup_api
    identity = submit(api, repository_id)
    assert submit(api, repository_id) == identity
    response = api.post("/runs", headers={"Idempotency-Key": "test"}, json={
        "repository_id": repository_id, "operation": "benchmark", "repeats": 2, "warmups": 0,
    })
    assert response.status_code == 409
    assert len(api.get("/runs").json()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", ["json", "compressed", "compact"])
async def test_solid_policy_survives_queue_and_archive_download(setup_api, tmp_path, metadata):
    import random

    from repoweave.archive.integrity import verify
    from repoweave.archive.manifest import HEADER

    settings, api, _, source = setup_api
    shared = random.Random(41).randbytes(4096)
    for index in range(16):
        (source / f"shared-{index:02}.bin").write_bytes(shared)
    registered = api.post("/repositories", json={"fixture": "owned"})
    assert registered.status_code == 201
    identity = submit(api, registered.json()["id"], policy={"solid_block_bytes": 65536,
                                                          "metadata_encoding": metadata})
    assert await run_once(settings)
    result = api.get(f"/runs/{identity}").json()
    assert result["status"] == "completed", result
    assert result["policy"]["solid_block_bytes"] == 65536
    artifact, = result["artifacts"]
    response = api.get(f"/artifacts/{artifact['id']}")
    assert response.status_code == 200
    assert HEADER.unpack(response.content[:HEADER.size])[1] == (3 if metadata == "json" else 4)
    archive = tmp_path / "solid.rpw"
    archive.write_bytes(response.content)
    unpack(archive, tmp_path / "restored")
    verify(source, tmp_path / "restored")


@pytest.mark.parametrize("body", [{"fixture": "/etc"}, {"fixture": "../outside"},
                                    {"path": "/etc"}, {"fixture": "unknown"}])
def test_invalid_fixture_paths(setup_api, body):
    _, api, _, _ = setup_api
    assert api.post("/repositories", json=body).status_code in {404, 422}


def test_unregistered_artifact_and_bad_inputs(setup_api):
    settings, api, repository_id, _ = setup_api
    unregistered = Store(settings.data_dir).artifact({"secret": "not published"})
    assert api.get(f"/artifacts/{unregistered}").status_code == 404
    assert api.get(f"/runs/{'0' * 64}").status_code == 404
    assert api.get("/runs/invalid").status_code == 422
    response = api.post("/runs", headers={"Idempotency-Key": "bad"}, json={
        "repository_id": repository_id, "operation": "pack", "server_path": "sentinel-secret"})
    assert response.status_code == 422 and "sentinel-secret" not in response.text
    assert api.post("/runs", json={"repository_id": repository_id, "operation": "pack"}).status_code == 422
    assert api.post("/runs", headers={"Idempotency-Key": "bad"}, json={
        "repository_id": repository_id, "operation": "optimize", "execution": "daytona"}).status_code == 422


@pytest.mark.asyncio
async def test_sse_sequence_replay_and_queued_cancel(setup_api):
    settings, api, repository_id, _ = setup_api
    identity = submit(api, repository_id)
    first = api.get(f"/runs/{identity}/events?follow=false").text
    last = int([line[4:] for line in first.splitlines() if line.startswith("id: ")][-1])
    assert api.post(f"/runs/{identity}/cancel").status_code == 202
    replay = api.get(f"/runs/{identity}/events", headers={"Last-Event-ID": str(last)}).text
    assert "cancelled" in replay
    assert all(int(line[4:]) > last for line in replay.splitlines() if line.startswith("id: "))
    assert not await run_once(settings)
    assert api.post(f"/runs/{identity}/resume").status_code == 409
    assert api.post(f"/runs/{identity}/approve").status_code == 409


@pytest.mark.asyncio
async def test_optimization_approval_resume_and_promote(setup_api):
    settings, api, repository_id, _ = setup_api
    identity = submit(api, repository_id, operation="optimize", policy={"codec": "stored"})
    assert api.post(f"/runs/{identity}/approve").status_code == 409
    assert await run_once(settings, agents=Agents())
    assert api.get(f"/runs/{identity}").json()["status"] == "awaiting-approval"
    assert api.post(f"/runs/{identity}/resume").status_code == 409
    assert api.post(f"/runs/{identity}/approve").status_code == 200
    assert api.post(f"/runs/{identity}/resume").status_code == 202
    assert await run_once(settings, agents=Agents())
    assert api.get(f"/runs/{identity}").json()["status"] == "promoted"
    assert len([e for e in Store(settings.data_dir).events(identity) if e["kind"] == "promoted"]) == 1
    assert not await run_once(settings)


@pytest.mark.asyncio
async def test_real_active_subprocess_cancellation(setup_api, monkeypatch):
    settings, api, repository_id, _ = setup_api
    identity = submit(api, repository_id, operation="benchmark", key="cancel")
    from repoweave.runs import worker
    original = asyncio.create_subprocess_exec
    started = asyncio.Event()
    processes = []

    async def capture(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", capture)
    task = asyncio.create_task(run_once(settings))
    await asyncio.wait_for(started.wait(), 10)
    assert api.post(f"/runs/{identity}/cancel").status_code == 202
    await asyncio.wait_for(task, 10)
    assert api.get(f"/runs/{identity}").json()["status"] == "cancelled"
    assert all(p.returncode is not None for p in processes)
    assert not api.get(f"/runs/{identity}").json()["artifacts"]


@pytest.mark.asyncio
async def test_worker_claim_recovery_and_exclusive_lock(setup_api):
    settings, api, repository_id, _ = setup_api
    identity = submit(api, repository_id)
    queue = Queue(Store(settings.data_dir))
    assert queue.take()["id"] == identity  # A killed worker left a durable running claim.
    with (settings.data_dir / "api-worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            await run_once(settings)
    assert await run_once(settings)
    assert api.get(f"/runs/{identity}").json()["status"] == "completed"


@pytest.mark.asyncio
async def test_artifact_tampering_and_symlinks_are_rejected(setup_api, tmp_path):
    settings, api, repository_id, _ = setup_api
    identity = submit(api, repository_id)
    await run_once(settings)
    queue = Queue(Store(settings.data_dir))
    artifact, = queue.downloads(identity)
    path = settings.data_dir / queue.download(artifact["id"])["path"]
    path.write_bytes(b"wrong")
    assert api.get(f"/artifacts/{artifact['id']}").status_code == 409
    path.unlink()
    outside = tmp_path / "private"
    outside.write_text("secret")
    path.symlink_to(outside)
    response = api.get(f"/artifacts/{artifact['id']}")
    assert response.status_code == 409 and "secret" not in response.text


def test_browser_origin_host_and_remote_access_are_rejected(setup_api):
    settings, api, _, _ = setup_api
    assert api.get("/health", headers={"Host": "evil.example"}).status_code == 400
    assert api.get("/health", headers={"Origin": "https://evil.example"}).status_code == 403
    assert api.get("/health", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert api.get("/health", headers={"Origin": "http://127.0.0.1:8000"}).status_code == 200
    with TestClient(create_app(settings), base_url="http://localhost",
                    client=("192.0.2.1", 1)) as remote:
        assert remote.get("/health").status_code == 403


def test_workflow_is_actual_graph(setup_api):
    from repoweave.workflows.improve_compression.graph import build_graph
    _, api, _, _ = setup_api
    assert api.get("/workflow").json() == build_graph().get_graph().to_json()


def test_worker_entrypoint_survives_api_disconnect(setup_api, tmp_path):
    settings, api, repository_id, _ = setup_api
    identity = submit(api, repository_id)
    result = subprocess.run([sys.executable, "-m", "repoweave.runs.worker", "--once"],
                            cwd=tmp_path, env={"REPOWEAVE_DATA_DIR": str(settings.data_dir)},
                            capture_output=True, timeout=30, check=False)
    assert result.returncode == 0
    with client(settings) as restarted:
        assert restarted.get(f"/runs/{identity}").json()["status"] == "completed"


@pytest.mark.asyncio
async def test_worker_failure_is_sanitized(setup_api, monkeypatch):
    settings, api, repository_id, _ = setup_api
    identity = submit(api, repository_id)
    from repoweave.runs import worker

    async def fail(*args, **kwargs):
        raise RuntimeError("sentinel-secret")
    monkeypatch.setattr(worker, "command", fail)
    assert await run_once(settings)
    assert api.get(f"/runs/{identity}").json()["status"] == "failed"
    assert "sentinel-secret" not in api.get(f"/runs/{identity}/events").text


@pytest.mark.asyncio
async def test_changed_registered_snapshot_fails_before_execution(setup_api):
    settings, api, repository_id, _ = setup_api
    queue = Queue(Store(settings.data_dir))
    path = settings.data_dir / queue.repository(repository_id)["source"] / "sample.py"
    path.chmod(0o600)
    path.write_text("changed")
    identity = submit(api, repository_id)
    await run_once(settings)
    assert api.get(f"/runs/{identity}").json()["status"] == "failed"


@pytest.mark.asyncio
async def test_interrupted_workflow_resumes_checkpoint(setup_api):
    settings, api, repository_id, _ = setup_api
    identity = submit(api, repository_id, operation="optimize", policy={"codec": "stored"})
    started = asyncio.Event()

    class WaitingAgents(Agents):
        async def analyze(self, *args):
            started.set()
            await asyncio.Future()

    task = asyncio.create_task(run_once(settings, agents=WaitingAgents()))
    await asyncio.wait_for(started.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert api.get(f"/runs/{identity}").json()["status"] == "interrupted"
    assert Store(settings.data_dir).run(identity)["status"] == "interrupted"
    assert api.post(f"/runs/{identity}/resume").status_code == 202
    assert await run_once(settings, agents=Agents())
    assert api.get(f"/runs/{identity}").json()["status"] == "awaiting-approval"


@pytest.mark.asyncio
async def test_concurrent_duplicates_share_one_job(setup_api):
    _, api, repository_id, _ = setup_api
    results = await asyncio.gather(*[
        asyncio.to_thread(submit, api, repository_id) for _ in range(6)
    ])
    assert len(set(results)) == 1
    assert len(api.get("/runs").json()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["pack", "benchmark", "optimize"])
async def test_registered_dictionary_assets_use_same_engine(setup_api, tmp_path, operation):
    from test_training_workflow import repository

    from repoweave.benchmarks.splits import Splits
    from repoweave.dictionaries.train import train_dictionaries
    from repoweave.repository import create_snapshot

    settings, _, _, _ = setup_api
    training, validation = tmp_path / "training", tmp_path / "validation"
    repository(training)
    repository(validation, validation=True)
    splits = Splits(train=["training"], validation=["validation"], test=["holdout"])
    train = create_snapshot(training, tmp_path / "captures")
    bundles = [train_dictionaries({"training": train}, splits, grouping=grouping, dictionary_size=2048)
               for grouping in ("graph", "extension")]
    for bundle in bundles:
        bundle.save(settings.data_dir / "dictionaries")
    settings = settings.model_copy(update={"fixtures": {"validation": validation}})
    with client(settings) as api:
        repository_id = api.post("/repositories", json={"fixture": "validation"}).json()["id"]
        identity = submit(api, repository_id, operation=operation,
                          policy={"dictionary_id": bundles[0].dictionary_id, "grouping": "graph"},
                          dictionary_ids=[b.dictionary_id for b in bundles], splits=splits.model_dump(),
                          partition_repository_id="validation")
        await run_once(settings, agents=Agents())
        result = api.get(f"/runs/{identity}").json()
        assert result["status"] in {"completed", "completed-without-improvement", "awaiting-approval"}
        artifact = next(a for a in result["artifacts"] if a["kind"] == (
            "benchmark" if operation == "benchmark" else "baseline" if operation == "optimize" else "archive"))
        response = api.get(f"/artifacts/{artifact['id']}")
        assert response.status_code == 200
        if operation == "pack":
            archive = tmp_path / "dictionary.rpw"
            archive.write_bytes(response.content)
            manifest = unpack(archive, tmp_path / "decoded")
            assert manifest.assets
            assert (tmp_path / "decoded/module00.py").read_bytes() == (validation / "module00.py").read_bytes()
        else:
            assert response.json()["results"][0]["samples"][0]["embedded_dictionary_bytes"] > 0
            assert response.json()["control_policy"]["grouping"] == "extension"
        rejected = api.post("/runs", headers={"Idempotency-Key": "wrong-provenance"}, json={
            "repository_id": repository_id, "operation": operation,
            "policy": {"dictionary_id": bundles[0].dictionary_id, "grouping": "graph"},
            "splits": splits.model_dump(), "partition_repository_id": "training",
        })
        assert rejected.status_code == 409
