"""Opt-in execution check of an existing, explicitly named engine sandbox."""

import asyncio
import json
import os
import uuid

import pytest
from daytona import AsyncDaytona, DaytonaConfig

from repoweave.config import load_settings
from repoweave.experiments.schemas import engine_id
from repoweave.routing.policy import write_json


@pytest.mark.live
@pytest.mark.asyncio
async def test_existing_daytona_engine_sandbox() -> None:
    name = os.environ.get("REPOWEAVE_LIVE_SANDBOX")
    if not name:
        pytest.skip("Set REPOWEAVE_LIVE_SANDBOX to an existing disposable test sandbox")
    settings = load_settings()
    if settings.daytona_api_key is None or settings.daytona_snapshot is None:
        pytest.fail("DAYTONA_API_KEY and REPOWEAVE_DAYTONA_SNAPSHOT are required", pytrace=False)
    output = settings.data_dir.resolve() / "live-daytona" / uuid.uuid4().hex
    output.mkdir(parents=True)
    report = {"sandbox_name": name, "status": "failed", "cleanup": "not-started"}
    try:
        async with AsyncDaytona(DaytonaConfig(
            api_key=settings.daytona_api_key.get_secret_value(), otel_enabled=False,
        )) as client:
            sandbox = await client.get(name, request_timeout=20)
            assert sandbox.name == name and sandbox.snapshot == settings.daytona_snapshot
            assert sandbox.state == "started", "The selected sandbox must already be running"
            assert (sandbox.cpu, sandbox.memory, sandbox.disk) == (2, 4, 10)
            # Stopping must preserve the user's existing sandbox and its files.
            assert sandbox.auto_delete_interval == -1, "Use a non-ephemeral test sandbox"
            report.update({"sandbox_id": sandbox.id, "snapshot": sandbox.snapshot,
                           "cpu": sandbox.cpu, "memory_gib": sandbox.memory,
                           "disk_gib": sandbox.disk})
            write_json(output / "started.json", report)
            try:
                async with asyncio.timeout(240):
                    response = await sandbox.process.exec(
                        "/usr/bin/env -i PATH=/usr/local/bin:/usr/bin:/bin "
                        "/usr/bin/timeout --signal=TERM --kill-after=5s 180s "
                        "/usr/local/bin/python /opt/repoweave/smoke_engine.py",
                        cwd="/home/repoweave", timeout=210,
                    )
                assert response.exit_code == 0, "Remote engine smoke command failed"
                assert len(response.result) < 65536, "Unexpected remote output size"
                result = json.loads(response.result)
                assert set(result) == {"status", "engine_id", "methods", "independent_decode", "v4_formats"}
                assert result["status"] == "ok"
                assert result["engine_id"] == engine_id(), "Installed engine revision differs"
                assert result["methods"] == ["rpw", "tar-zstd", "tar-lzma"]
                assert result["independent_decode"] is True
                assert result["v4_formats"] == ["solid-zstd-compressed", "solid-zstd-compact",
                                                 "solid-lzma-compact"]
                report.update({"status": "passed", "exit_code": 0, "engine": result})
            finally:
                # Remote timeout is separate from the API timeout; stop is the final backstop.
                await sandbox.stop(timeout=60)
                stopped = await client.get(sandbox.id, request_timeout=20)
                assert stopped.state == "stopped", "Sandbox did not stop"
                report["cleanup"] = "stopped-and-preserved"
    except Exception as error:  # noqa: BLE001 - do not expose SDK credentials or response bodies
        report["status"] = "failed"
        report["error_type"] = type(error).__name__
        pytest.fail(f"Live Daytona check failed ({type(error).__name__}); see local report",
                    pytrace=False)
    finally:
        write_json(output / "report.json", report)
        print(f"Live Daytona report: {output / 'report.json'}")
