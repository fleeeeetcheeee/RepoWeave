"""V4 showcase with real engine jobs, mocked transport or explicitly opted-in Daytona."""

import os
import random
import uuid

import pytest
from test_optimization import Cloud

from repoweave.archive.decode import unpack
from repoweave.archive.integrity import verify
from repoweave.archive.manifest import HEADER
from repoweave.archive.metadata import PREFIX
from repoweave.archive.reader import read_manifest
from repoweave.config import Settings, load_settings
from repoweave.execution.daytona import Remote
from repoweave.experiments.runner import Runner
from repoweave.experiments.schemas import engine_id, file_hash
from repoweave.repository import create_snapshot
from repoweave.routing.objective import Limits
from repoweave.routing.policy import write_json
from repoweave.routing.schemas import Policy
from repoweave.runs.service import submit
from repoweave.storage.database import Store


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, pytest.param(True, marks=pytest.mark.live)])
@pytest.mark.parametrize("codec,metadata", [("zstd", "compressed"), ("zstd", "compact"),
                                           ("lzma", "compact")])
async def test_v4_cloud_showcase(tmp_path, live, codec, metadata):
    if live and os.environ.get("REPOWEAVE_LIVE_V4") != "1":
        pytest.skip("Set REPOWEAVE_LIVE_V4=1 to authorize owned fixtures and two sandboxes per case")
    configured = load_settings() if live else Settings(
        DAYTONA_API_KEY="fake", REPOWEAVE_DAYTONA_SNAPSHOT="engine")
    output = (configured.data_dir.resolve() / "live-v4" / f"{codec}-{metadata}-{uuid.uuid4().hex}"
              if live else tmp_path)
    source = output / "owned-fixture"
    source.mkdir(parents=True)
    shared = random.Random(4).randbytes(4096)
    for index in range(32):
        (source / f"shared-{index:02d}.bin").write_bytes(shared + bytes([index]))
    (source / "shared-00.bin").chmod(0o751)
    (source / "empty-dir").mkdir()
    (source / "empty-file").touch()
    (source / "unicode-λ.txt").write_bytes("Owned synthetic fixture: λ\r\n".encode())
    snapshot = create_snapshot(source, output / "captures")
    settings = configured.model_copy(update={
        "data_dir": output / "state", "execution": "daytona", "allow_remote_data": True,
        "langsmith_tracing": False, "experiment_timeout_seconds": 120,
    })
    policy = Policy(codec=codec, metadata_encoding=metadata, solid_block_bytes=16384)
    receipt = {"status": "failed", "boundary": "live Daytona" if live else "mock Daytona",
               "engine_id": engine_id(), "daytona_snapshot": settings.daytona_snapshot,
               "policy": policy.model_dump(), "snapshot_id": snapshot.snapshot_id}
    runner = None
    try:
        run_id = submit(source, policy, settings, approved_snapshot=snapshot.snapshot_id,
                        repeats=2, warmups=1,
                        limits=Limits(encode_seconds=30., decode_seconds=30.,
                                      peak_memory_bytes=1024 * 1024 * 1024))
        receipt["run_id"] = run_id
        store = Store(settings.data_dir)
        cloud = None if live else Cloud(output / "fake-cloud")
        remote = Remote(store, run_id, settings, client=cloud)
        runner = Runner(store, run_id, settings, remote=remote)
        store.status(run_id, "running")
        report = await runner.report(policy)
        assert all(row["eligible"] and all(s["reconstructed"] for s in row["samples"])
                   for row in report["results"])
        assert all(len({s["archive_bytes"] for s in row["samples"]}) == 1 for row in report["results"])
        await runner.independent_decode(report)
        archive = runner.root / "selected.rpw"
        with archive.open("rb") as stream:
            version = HEADER.unpack(stream.read(HEADER.size))[1]
            representation, compression, _ = PREFIX.unpack(stream.read(PREFIX.size))
            stream.seek(0)
            manifest = read_manifest(stream)
        assert version == 4
        assert representation == (0 if metadata == "compressed" else 1)
        assert compression == 1
        assert len(manifest.blocks) > 1 and any(b.codec == codec for b in manifest.blocks)
        assert all(b.size <= policy.solid_block_bytes for b in manifest.blocks)
        unpack(runner.root / "decoder" / "restored.rpw", output / "restored")
        verify(source, output / "restored")
        independent = store.read_artifact(store.phase(run_id, "independent-decode"))
        assert independent["snapshot_id"] == snapshot.snapshot_id
        assert independent["engine_id"] == engine_id()
        await runner.close()
        roles = {role: store.sandbox(run_id, role) for role in ("experiment", "decoder")}
        assert all(row["status"] == "deleted" for row in roles.values())
        assert roles["experiment"]["sandbox_id"] != roles["decoder"]["sandbox_id"]
        if cloud:
            assert cloud.created[1].fs.uploads == ["/home/repoweave/jobs/decode/archive.bin"]
        store.status(run_id, "completed-without-improvement")
        receipt.update(status="passed", benchmark=report, sandboxes=roles,
                       archive=str(archive), archive_sha256=file_hash(archive), archive_version=version,
                       metadata_representation=representation, block_count=len(manifest.blocks),
                       independent_decode=independent, exact_local_bytes=True)
    except Exception as error:  # SDK payloads must not enter logs.
        receipt["error_type"] = type(error).__name__
        if not live:
            raise
        pytest.fail(f"V4 cloud showcase failed ({type(error).__name__}); see receipt", pytrace=False)
    finally:
        if runner:
            try:
                await runner.close()
            except Exception:  # noqa: BLE001 - durable cleanup intent remains available
                receipt.update(status="failed", cleanup_pending=True)
            receipt["sandboxes"] = {r: runner.store.sandbox(runner.run_id, r)
                                    for r in ("experiment", "decoder")}
        write_json(output / "report.json", receipt)
        if live:
            print(f"V4 cloud receipt: {output / 'report.json'}")
    assert receipt["status"] == "passed"
