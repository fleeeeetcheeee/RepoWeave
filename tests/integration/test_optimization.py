"""Real engine + durable graph; fake reasoning and cloud transport boundaries."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from daytona.common.errors import DaytonaNotFoundError

from repoweave.agents.schemas import Candidate, CandidatePolicy, Diagnosis, Hypothesis, Proposals
from repoweave.config import Settings
from repoweave.execution import local
from repoweave.execution.daytona import Remote
from repoweave.execution.experiment_entrypoint import decode_only
from repoweave.experiments.runner import Runner
from repoweave.repository import create_snapshot
from repoweave.routing.objective import Limits
from repoweave.routing.schemas import Policy
from repoweave.runs import service
from repoweave.storage.database import Store


class Agents:
    def __init__(self):
        self.calls = []

    async def analyze(self, evidence, settings):
        self.calls.append("analyze")
        ids = [evidence.results[0].result_id]
        return Diagnosis(summary="Synthetic diagnosis", evidence_ids=ids,
                         hypotheses=[Hypothesis(explanation="Try zstd", uncertainty="high", evidence_ids=ids)])

    async def propose(self, evidence, diagnosis, parent, settings, **kwargs):
        self.calls.append("propose")
        return Proposals(candidates=[Candidate(parent_policy_id=parent.policy_id,
                        policy=CandidatePolicy(), rationale="Test measured compression",
                        evidence_ids=diagnosis.evidence_ids)])


class FS:
    def __init__(self, root):
        self.root = root
        self.uploads = []
        self.fail_download = False

    def path(self, path):
        return self.root / path.removeprefix("/home/repoweave/")

    async def create_folder(self, path, mode):
        self.path(path).mkdir(parents=True, exist_ok=True)

    async def upload_file(self, source, destination, **kwargs):
        self.uploads.append(destination)
        self.path(destination).write_bytes(Path(source).read_bytes())

    async def get_file_info(self, path, **kwargs):
        if self.fail_download:
            raise ValueError("export failed sentinel")
        return SimpleNamespace(size=self.path(path).stat().st_size, is_dir=False)

    async def download_file_stream(self, path, **kwargs):
        async def chunks():
            yield self.path(path).read_bytes()
        return chunks()


class Sandbox:
    def __init__(self, params, root):
        self.id = params.name
        self.name = params.name
        self.labels = params.labels
        self.snapshot = params.snapshot
        self.cpu, self.memory, self.disk = 2, 4, 10
        self.state = "started"
        self.fs = FS(root)
        self.process = self
        self.commands = []
        self.fail = False

    async def exec(self, command, **kwargs):
        self.commands.append(command)
        assert command.startswith("/usr/bin/env -i ")
        operation, directory = command.split()[-2:]
        work = self.fs.path(directory)
        if self.fail:
            return SimpleNamespace(exit_code=1, result="untrusted secret error")
        if operation == "measure":
            await local.execute(work, 60)
        else:
            await asyncio.to_thread(decode_only, work)
        return SimpleNamespace(exit_code=0, result="")

    async def stop(self, **kwargs):
        self.state = "stopped"


class Cloud:
    def __init__(self, root):
        self.root = root
        self.created = []
        self.deleted = []
        self.fail_create = False

    async def create(self, params, **kwargs):
        assert not params.env_vars and params.network_block_all
        assert params.ttl_minutes == 10 and params.auto_stop_interval == 10
        assert len(self.created) < 2
        if self.created:
            assert self.created[0].id in self.deleted
        sandbox = Sandbox(params, self.root / str(len(self.created)))
        self.created.append(sandbox)
        if self.fail_create:
            raise TimeoutError("uncertain creation")
        return sandbox

    async def get(self, identity, **kwargs):
        for sandbox in self.created:
            if sandbox.id == identity and identity not in self.deleted:
                return sandbox
        raise DaytonaNotFoundError("not found")

    async def delete(self, sandbox, **kwargs):
        self.deleted.append(sandbox.id)


@pytest.fixture
def setup_run(tmp_path):
    source = tmp_path / "fixture"
    source.mkdir()
    (source / "sample.py").write_bytes(b"raise RuntimeError('never execute fixture')\n" * 300)
    (source / "empty").mkdir()
    (source / "binary.bin").write_bytes(bytes(range(256)))
    (source / "sample.py").chmod(0o755)
    settings = Settings(REPOWEAVE_DATA_DIR=tmp_path / "state", REPOWEAVE_MAX_CANDIDATES=1)
    return source, settings


def submit(setup_run, *, cloud=False, parent=None, limits=None):
    source, settings = setup_run
    if cloud:
        settings = settings.model_copy(update={"execution": "daytona", "allow_remote_data": True,
                                                "daytona_snapshot": "engine"})
        settings = Settings.model_validate({**settings.model_dump(by_alias=True), "DAYTONA_API_KEY": "fake"})
    snapshot = create_snapshot(source, settings.data_dir / "captures")
    run_id = service.submit(source, parent or Policy(codec="stored"), settings, repeats=1, warmups=0,
                            approved_snapshot=snapshot.snapshot_id if cloud else None, limits=limits)
    return settings, Store(settings.data_dir), run_id


@pytest.mark.asyncio
async def test_local_improvement_approval_restart_and_idempotency(setup_run):
    settings, store, run_id = submit(setup_run)
    agents = Agents()
    assert (await service.execute(store, run_id, settings, agents=agents))["status"] == "awaiting-approval"
    root = store.root / "runs" / run_id
    archive = (root / "selected.rpw").read_bytes()
    mtimes = {p: p.stat().st_mtime_ns for p in root.glob("jobs/*/result.json")}
    await service.approve(store, run_id)
    assert (await service.execute(Store(store.root), run_id, settings, agents=agents))["status"] == "promoted"
    await service.execute(store, run_id, settings, agents=agents)
    assert agents.calls == ["analyze", "propose"]
    assert (root / "selected.rpw").read_bytes() == archive
    assert all(p.stat().st_mtime_ns == mtime for p, mtime in mtimes.items())
    assert len([e for e in store.events(run_id) if e["kind"] == "promoted"]) == 1
    with pytest.raises(ValueError):
        await service.approve(store, run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("limited", [False, True])
async def test_no_gain_and_resource_ineligibility(setup_run, limited):
    limits = Limits(peak_memory_bytes=1) if limited else None
    settings, store, run_id = submit(setup_run, parent=Policy(), limits=limits)
    row = await service.execute(store, run_id, settings, agents=Agents())
    assert row["status"] == "completed-without-improvement"
    with pytest.raises(ValueError):
        store.approve(run_id)


@pytest.mark.asyncio
async def test_cloud_roundtrip_export_and_separate_decoder(setup_run, tmp_path):
    settings, store, run_id = submit(setup_run, cloud=True)
    cloud = Cloud(tmp_path / "cloud")
    remote = Remote(store, run_id, settings, cloud)
    row = await service.execute(store, run_id, settings, agents=Agents(), remote=remote)
    assert row["status"] == "awaiting-approval"
    assert len(cloud.created) == len(cloud.deleted) == 2
    assert cloud.created[1].fs.uploads == ["/home/repoweave/jobs/decode/archive.bin"]
    assert store.phase(run_id, "independent-decode")
    for role in ("experiment", "decoder"):
        assert store.sandbox(run_id, role)["status"] == "deleted"
    events = store.events(run_id)
    assert [e["sequence"] for e in events] == sorted({e["sequence"] for e in events})


@pytest.mark.asyncio
async def test_cancel_active_job_cleans_cloud(setup_run, tmp_path):
    settings, store, run_id = submit(setup_run, cloud=True)
    cloud = Cloud(tmp_path / "cloud")
    remote = Remote(store, run_id, settings, cloud)
    sandbox = await remote.sandbox("experiment")
    started = asyncio.Event()

    async def hanging(*args, **kwargs):
        started.set()
        await asyncio.Future()

    sandbox.exec = hanging
    task = asyncio.create_task(service.execute(store, run_id, settings, agents=Agents(), remote=remote))
    await asyncio.wait_for(started.wait(), 10)
    store.cancel(run_id)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    assert store.run(run_id)["status"] == "cancelled"
    assert cloud.deleted == [sandbox.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["create", "command", "export", "corruption", "oversize"])
async def test_remote_failures_never_promote_and_cleanup(setup_run, tmp_path, failure):
    settings, store, run_id = submit(setup_run, cloud=True)
    cloud = Cloud(tmp_path / "cloud")
    remote = Remote(store, run_id, settings, cloud)
    if failure == "create":
        cloud.fail_create = True
    else:
        sandbox = await remote.sandbox("experiment")
        sandbox.fail = failure == "command"
        sandbox.fs.fail_download = failure == "export"
        if failure == "oversize":
            sandbox.fs.get_file_info = AsyncMock(return_value=SimpleNamespace(size=10**12, is_dir=False))
        if failure == "corruption":
            original = sandbox.fs.download_file_stream

            async def corrupt(path, **kwargs):
                if path.endswith("archive.bin"):
                    async def chunks():
                        yield b"corrupted"
                    return chunks()
                return await original(path, **kwargs)

            sandbox.fs.download_file_stream = corrupt
    with pytest.raises(ValueError, match="Optimization failed"):
        await service.execute(store, run_id, settings, agents=Agents(), remote=remote)
    assert store.run(run_id)["status"] == "failed"
    assert len(cloud.deleted) == 1
    assert not any(e["kind"] == "promoted" for e in store.events(run_id))
    assert "secret" not in json.dumps(store.events(run_id))


@pytest.mark.asyncio
async def test_completed_job_replay_and_artifact_tamper(setup_run):
    settings, store, run_id = submit(setup_run)
    runner = Runner(store, run_id, settings)
    await runner.prepare()
    request = runner.request(Policy(), "rpw", 0)
    result = await runner.job(request)
    assert await runner.job(request) == result
    (runner.root / "jobs" / request.job_id / "archive.bin").write_bytes(b"broken")
    with pytest.raises(ValueError):
        await runner.job(request)


def test_duplicate_submission_and_conflicting_parent(setup_run):
    source, _ = setup_run
    settings, store, run_id = submit(setup_run)
    assert service.submit(source, Policy(codec="stored"), settings, run_id=run_id,
                          repeats=1, warmups=0) == run_id
    with pytest.raises(ValueError):
        service.submit(source, Policy(), settings, run_id=run_id, repeats=1, warmups=0)
    assert len(store.runs()) == 1


@pytest.mark.asyncio
async def test_stale_promotion_rejected(setup_run):
    settings, store, run_id = submit(setup_run)
    await service.execute(store, run_id, settings, agents=Agents())
    await service.approve(store, run_id)
    with store.connect() as db:
        db.execute("UPDATE active SET policy_id=?", ("f" * 64,))
    with pytest.raises(ValueError):
        await service.execute(store, run_id, settings, agents=Agents())
    assert not any(e["kind"] == "promoted" for e in store.events(run_id))


@pytest.mark.asyncio
async def test_unknown_creation_remains_pending_until_reconciled(setup_run, tmp_path):
    settings, store, run_id = submit(setup_run, cloud=True)
    cloud = Cloud(tmp_path / "cloud")
    original_create = cloud.create
    cloud.create = AsyncMock(side_effect=TimeoutError)
    remote = Remote(store, run_id, settings, cloud)
    with pytest.raises(TimeoutError):
        await remote.sandbox("experiment")
    with pytest.raises(ValueError, match="cleanup pending"):
        await remote.close()
    assert store.sandbox(run_id, "experiment")["status"] == "cleanup-pending"
    # The original request eventually creates a sandbox. Cleanup must find it by name.
    from daytona import CreateSandboxFromSnapshotParams

    row = store.sandbox(run_id, "experiment")
    await original_create(CreateSandboxFromSnapshotParams(
        name=row["name"], snapshot="engine", env_vars={}, network_block_all=True,
        ttl_minutes=10, auto_stop_interval=10, labels={"repoweave-run": run_id},
    ))
    await remote.close()
    assert store.sandbox(run_id, "experiment")["status"] == "deleted"


@pytest.mark.asyncio
async def test_restart_exports_finished_remote_job_without_reexecution(setup_run, tmp_path):
    settings, store, run_id = submit(setup_run, cloud=True)
    cloud = Cloud(tmp_path / "cloud")
    remote = Remote(store, run_id, settings, cloud)
    runner = Runner(store, run_id, settings, remote)
    await runner.prepare()
    request = runner.request(Policy(), "rpw", 0)
    work = runner.root / "jobs" / request.job_id
    local.stage(work, request, runner.source, runner.registry)
    store.claim_job(run_id, request.job_id)
    await remote.execute(work, request)
    (work / "result.json").unlink()
    (work / "archive.bin").unlink()
    resumed = Runner(Store(store.root), run_id, settings, Remote(store, run_id, settings, cloud))
    assert (await resumed.job(request)).job_id == request.job_id
    assert len(cloud.created[0].commands) == 1
    await resumed.close()


@pytest.mark.asyncio
async def test_candidate_budget_and_bad_evidence_stop_before_experiment(setup_run):
    settings, store, run_id = submit(setup_run)
    agents = Agents()
    original = agents.propose

    async def too_many(*args, **kwargs):
        result = await original(*args, **kwargs)
        return Proposals(candidates=result.candidates * 2)

    agents.propose = too_many
    with pytest.raises(ValueError):
        await service.execute(store, run_id, settings, agents=agents)
    assert not any(e["data"].get("node") == "experiment" for e in store.events(run_id))


@pytest.mark.asyncio
async def test_graph_blocks_automatic_tracing_with_secret_context(setup_run, monkeypatch):
    from langsmith import Client

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "sentinel-secret")
    calls = []
    monkeypatch.setattr(Client, "create_run", lambda *a, **kw: calls.append(kw))
    settings, store, run_id = submit(setup_run, parent=Policy())
    from pydantic import SecretStr

    settings = settings.model_copy(update={"factory_api_key": SecretStr("sentinel-runtime-context")})
    await service.execute(store, run_id, settings, agents=Agents())
    assert calls == []
    assert b"sentinel-runtime-context" not in (store.root / "checkpoints.sqlite").read_bytes()


@pytest.mark.asyncio
async def test_cleanup_failure_is_retryable_and_no_third_sandbox(setup_run, tmp_path, monkeypatch):
    monkeypatch.setattr("repoweave.execution.daytona.CLEANUP_CONFIRM_SECONDS", 0.01)
    settings, store, run_id = submit(setup_run, cloud=True)
    cloud = Cloud(tmp_path / "cloud")
    remote = Remote(store, run_id, settings, cloud)
    await remote.sandbox("experiment")
    original_delete = cloud.delete
    cloud.delete = AsyncMock(side_effect=TimeoutError)
    with pytest.raises(ValueError, match="cleanup pending"):
        await remote.close()
    assert store.sandbox(run_id, "experiment")["status"] == "cleanup-pending"
    with pytest.raises(ValueError, match="retired"):
        await remote.sandbox("experiment")
    cloud.delete = original_delete
    await remote.close()
    assert len(cloud.created) == 1


@pytest.mark.asyncio
async def test_expired_sandbox_budget_cleans_without_dispatch(setup_run, tmp_path, monkeypatch):
    settings, store, run_id = submit(setup_run, cloud=True)
    cloud = Cloud(tmp_path / "cloud")
    remote = Remote(store, run_id, settings, cloud)
    sandbox = await remote.sandbox("experiment")
    import time

    monkeypatch.setattr("repoweave.execution.daytona.time.time", lambda: time.monotonic() + 10**12)
    with pytest.raises(ValueError, match="Optimization failed"):
        await service.execute(store, run_id, settings, agents=Agents(), remote=remote)
    assert sandbox.commands == [] and cloud.deleted == [sandbox.id]


@pytest.mark.asyncio
async def test_stop_failure_does_not_fail_confirmed_deletion(setup_run, tmp_path):
    from daytona.common.errors import DaytonaForbiddenError

    settings, store, run_id = submit(setup_run, cloud=True)
    cloud = Cloud(tmp_path / "cloud")
    remote = Remote(store, run_id, settings, cloud)
    sandbox = await remote.sandbox("experiment")
    sandbox.stop = AsyncMock(side_effect=DaytonaForbiddenError("secret API payload"))
    await remote.finish_experiments()
    assert store.sandbox(run_id, "experiment")["status"] == "deleted"
    assert cloud.deleted == [sandbox.id]
    assert "secret" not in json.dumps(store.events(run_id))


@pytest.mark.asyncio
async def test_checkpoint_errors_do_not_capture_sdk_payloads(setup_run):
    settings, store, run_id = submit(setup_run)
    agents = Agents()
    agents.analyze = AsyncMock(side_effect=ValueError("sentinel-sdk-exception-payload"))
    with pytest.raises(ValueError):
        await service.execute(store, run_id, settings, agents=agents)
    assert b"sentinel-sdk-exception-payload" not in (store.root / "checkpoints.sqlite").read_bytes()
    assert "sentinel-sdk-exception-payload" not in json.dumps(store.events(run_id))


@pytest.mark.asyncio
async def test_deletion_waits_for_auto_delete_after_explicit_delete_denied(setup_run, tmp_path):
    from daytona.common.errors import DaytonaForbiddenError

    settings, store, run_id = submit(setup_run, cloud=True)
    cloud = Cloud(tmp_path / "cloud")
    remote = Remote(store, run_id, settings, cloud)
    sandbox = await remote.sandbox("experiment")
    original_delete = cloud.delete

    async def stop_and_schedule_delete(**kwargs):
        asyncio.get_running_loop().call_later(0.01, cloud.deleted.append, sandbox.id)
        sandbox.state = "stopped"

    sandbox.stop = stop_and_schedule_delete
    cloud.delete = AsyncMock(side_effect=DaytonaForbiddenError("denied"))
    await remote.finish_experiments()
    assert store.sandbox(run_id, "experiment")["status"] == "deleted"
    cloud.delete = original_delete
