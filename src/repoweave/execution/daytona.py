"""Two sequential, bounded engine sandboxes with durable creation/cleanup intent."""

import asyncio
import json
import time
from pathlib import Path

from daytona import AsyncDaytona, CreateSandboxFromSnapshotParams, DaytonaConfig
from daytona.common.errors import DaytonaNotFoundError

from repoweave.archive.decode import unpack
from repoweave.benchmarks.worker import _compare
from repoweave.config import ConfigurationError, Settings
from repoweave.experiments.schemas import MAX_ARTIFACT_BYTES, Request, file_hash
from repoweave.repository import create_snapshot
from repoweave.storage.database import Store

ROOT = "/home/repoweave/jobs"
CLEANUP_CONFIRM_SECONDS = 30


class Remote:
    def __init__(self, store: Store, run_id: str, settings: Settings, client=None):
        self.store, self.run_id, self.settings = store, run_id, settings
        self.spec = store.run(run_id)["spec"]
        self.client = client
        self.owns_client = client is None
        self.handles: dict = {}

    async def connect(self):
        if self.client is None:
            if self.settings.daytona_api_key is None:
                raise ConfigurationError("DAYTONA_API_KEY is required for Daytona jobs")
            self.client = AsyncDaytona(DaytonaConfig(
                api_key=self.settings.daytona_api_key.get_secret_value(), otel_enabled=False,
            ))
            await self.client.__aenter__()
        return self.client

    async def sandbox(self, role: str):
        self.settings.validate_daytona(dataset_id=self.spec["snapshot_id"],
                                      approved_dataset_ids=self.spec["approved_dataset_ids"])
        if self.spec["daytona_snapshot"] != self.settings.daytona_snapshot:
            raise ValueError("Configured snapshot changed")
        client = await self.connect()
        row = self.store.sandbox(self.run_id, role)
        if row["status"] in ("deleted", "cleanup-pending"):
            raise ValueError("Sandbox is retired; start a new run")
        if role in self.handles:
            return self.handles[role]
        if role == "decoder" and self.store.sandbox(self.run_id, "experiment")["status"] != "deleted":
            raise ValueError("Experiment sandbox must be deleted before decoding")
        if row["status"] == "planned":
            self.store.sandbox(self.run_id, role, status="creating")
            self.store.phase(self.run_id, f"{role}-deadline", {"expires": time.time() + 600})
            sandbox = await client.create(CreateSandboxFromSnapshotParams(
                name=row["name"], snapshot=self.settings.daytona_snapshot,
                labels={"repoweave-run": self.run_id, "repoweave-role": role},
                public=False, env_vars={}, network_block_all=True,
                auto_stop_interval=10, auto_delete_interval=0, ttl_minutes=10,
            ), timeout=60)
            self.store.sandbox(self.run_id, role, sandbox_id=sandbox.id, status="started")
        else:
            # An uncertain creation is looked up by its persisted unique name, never recreated.
            sandbox = await client.get(row["sandbox_id"] or row["name"], request_timeout=20)
            self.store.sandbox(self.run_id, role, sandbox_id=sandbox.id, status="started")
        self.handles[role] = sandbox
        if (sandbox.snapshot != self.settings.daytona_snapshot
                or (sandbox.cpu, sandbox.memory, sandbox.disk) != (2, 4, 10)
                or sandbox.state != "started"
                or sandbox.labels.get("repoweave-run") != self.run_id):
            raise ValueError("Sandbox identity, state or resource budget differs")
        self.remaining(role)
        return sandbox

    def remaining(self, role: str) -> float:
        identity = self.store.phase(self.run_id, f"{role}-deadline")
        assert identity is not None
        remaining = self.store.read_artifact(identity)["expires"] - time.time()
        if remaining <= 0:
            raise TimeoutError("Sandbox time budget exhausted")
        return min(remaining, 600)

    async def download(self, sandbox, remote: str, local: Path, limit: int) -> None:
        info = await sandbox.fs.get_file_info(remote, request_timeout=20)
        if info.is_dir or not 0 <= info.size <= limit:
            raise ValueError("Remote artifact exceeds byte limit or is not a file")
        if local.exists():
            raise ValueError("Artifact destination already exists")
        partial = local.with_suffix(local.suffix + ".partial")
        try:
            from contextlib import aclosing

            stream = await sandbox.fs.download_file_stream(remote, timeout=30)
            size = 0
            async with aclosing(stream):
                with partial.open("wb") as output:
                    async for chunk in stream:
                        size += len(chunk)
                        if size > limit or size > info.size:
                            raise ValueError("Downloaded artifact exceeds declared size")
                        output.write(chunk)
            if partial.stat().st_size != info.size:
                raise ValueError("Downloaded artifact size differs")
            file_hash(partial, limit)
            partial.replace(local)
        finally:
            partial.unlink(missing_ok=True)

    async def command(self, sandbox, role: str, operation: str, work: str) -> None:
        seconds = max(1, int(min(self.remaining(role), self.settings.experiment_timeout_seconds)))
        response = await sandbox.process.exec(
            "/usr/bin/env -i PATH=/usr/local/bin:/usr/bin:/bin "
            f"/usr/bin/timeout --signal=TERM --kill-after=5s {seconds}s "
            f"/usr/local/bin/python -m repoweave.execution.experiment_entrypoint {operation} {work}",
            cwd="/home/repoweave", timeout=seconds + 10,
        )
        if response.exit_code != 0:
            raise ValueError("Remote engine operation failed")

    async def execute(self, work: Path, request: Request, *, resume: bool = False) -> None:
        sandbox = await self.sandbox("experiment")
        remote = f"{ROOT}/{request.job_id}"
        async with asyncio.timeout(self.remaining("experiment")):
            if not resume:
                await sandbox.fs.create_folder(remote, "700")
                for name in ("input.rpw", "request.json"):
                    await sandbox.fs.upload_file(str(work / name), f"{remote}/{name}", timeout=30)
                if request.policy.dictionary_id:
                    await sandbox.fs.create_folder(f"{remote}/dictionaries", "700")
                    # Only validated, selected bundle files produced by local.stage.
                    for path in sorted((work / "dictionaries").rglob("*")):
                        target = f"{remote}/{path.relative_to(work).as_posix()}"
                        if path.is_dir():
                            await sandbox.fs.create_folder(target, "700")
                        elif path.is_file() and not path.is_symlink():
                            await sandbox.fs.upload_file(str(path), target, timeout=30)
                        else:
                            raise ValueError("Invalid dictionary staging file")
                await self.command(sandbox, "experiment", "measure", remote)
            # Resume exports a finished result; it never blindly re-executes an uncertain job.
            for name, limit in (("archive.bin", MAX_ARTIFACT_BYTES), ("result.json", 65536)):
                destination = work / name
                if not destination.exists():
                    await self.download(sandbox, f"{remote}/{name}", destination, limit)

    async def cleanup(self, role: str) -> None:
        row = self.store.sandbox(self.run_id, role)
        if row["status"] in ("planned", "deleted"):
            return
        self.store.sandbox(self.run_id, role, status="cleanup-pending")
        client = await self.connect()
        try:
            sandbox = await client.get(row["sandbox_id"] or row["name"], request_timeout=20)
        except DaytonaNotFoundError:
            if row["sandbox_id"] is None:
                # A timed-out creation can become visible later; retain reconciliation intent.
                identity = self.store.phase(self.run_id, f"{role}-deadline")
                if identity and self.store.read_artifact(identity)["expires"] > time.time():
                    raise ValueError("Uncertain creation still requires reconciliation") from None
            self.store.sandbox(self.run_id, role, status="deleted")
            return
        if sandbox.labels.get("repoweave-run") != self.run_id or sandbox.name != row["name"]:
            raise ValueError("Refusing cleanup of an unrelated sandbox")
        try:
            await sandbox.stop(timeout=30)
        except Exception:  # noqa: BLE001 - confirmed deletion below is the cleanup criterion
            self.store.event(self.run_id, "sandbox-stop-failed", {"role": role})
        try:
            await client.delete(sandbox, timeout=30, wait=True)
        except DaytonaNotFoundError:
            pass  # auto-delete may have removed the sandbox when it stopped
        except Exception:  # noqa: BLE001 - stop can trigger asynchronous auto-deletion
            # Some keys can stop but cannot explicitly delete. Confirm provider auto-deletion;
            # neither access denied nor a successful stop is itself proof of deletion.
            try:
                async with asyncio.timeout(CLEANUP_CONFIRM_SECONDS):
                    while True:
                        try:
                            await client.get(sandbox.id, request_timeout=20)
                        except DaytonaNotFoundError:
                            break
                        await asyncio.sleep(0.5)
            except Exception:  # noqa: BLE001 - preserve reconciliation intent without SDK payloads
                raise ValueError("Sandbox deletion was not confirmed") from None
        self.handles.pop(role, None)
        self.store.sandbox(self.run_id, role, status="deleted")

    async def finish_experiments(self) -> None:
        await self.cleanup("experiment")

    async def independent_decode(self, archive: Path, snapshot_id: str) -> None:
        receipt = self.store.phase(self.run_id, "independent-decode")
        root = self.store.root / "runs" / self.run_id
        work = root / "decoder"
        work.mkdir(exist_ok=True)
        if receipt is None:
            row = self.store.sandbox(self.run_id, "decoder")
            fresh = row["status"] == "planned"
            sandbox = await self.sandbox("decoder")
            remote = f"{ROOT}/decode"
            async with asyncio.timeout(self.remaining("decoder")):
                if fresh:
                    await sandbox.fs.create_folder(remote, "700")
                    # No original snapshot, request, registry, or credentials enter this sandbox.
                    await sandbox.fs.upload_file(str(archive), f"{remote}/archive.bin", timeout=30)
                    await self.command(sandbox, "decoder", "decode", remote)
                for name, limit in (("restored.rpw", MAX_ARTIFACT_BYTES), ("decoded.json", 65536)):
                    if not (work / name).exists():
                        await self.download(sandbox, f"{remote}/{name}", work / name, limit)
        result = json.loads((work / "decoded.json").read_bytes())
        if (set(result) != {"snapshot_id", "archive_sha256", "engine_id"}
                or result["snapshot_id"] != snapshot_id
                or result["engine_id"] != self.spec["engine_id"]
                or file_hash(work / "restored.rpw") != result["archive_sha256"]):
            raise ValueError("Independent decoder identities differ")
        import tempfile

        with tempfile.TemporaryDirectory(dir=work) as temporary:
            restored = Path(temporary) / "restored"
            unpack(work / "restored.rpw", restored)
            snapshot = create_snapshot(root / "input", root / "captures")
            _compare(snapshot, restored)
        self.store.phase(self.run_id, "independent-decode", result)
        await self.cleanup("decoder")

    async def close(self) -> None:
        failures = []
        for role in ("experiment", "decoder"):
            try:
                await self.cleanup(role)
            except Exception:  # noqa: BLE001 - retain durable intent for reconciliation
                failures.append(role)
        if self.client is not None and self.owns_client:
            await self.client.__aexit__(None, None, None)
            self.client = None
        if failures:
            raise ValueError("Sandbox cleanup pending; run runs cleanup")
