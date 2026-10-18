import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from repoweave.execution.experiment_entrypoint import read_json_file
from repoweave.experiments.schemas import engine_sources, environment

ROOT = Path(__file__).resolve().parents[2]


def script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_engine_sources_exclude_services_and_configuration() -> None:
    paths = engine_sources(ROOT / "src" / "repoweave")
    assert paths
    assert all(path.suffix == ".py" for path in paths)
    assert not any(path.name in ("config.py", "cli.py", "local.py") for path in paths)
    assert not any(part in ("agents", "storage", "workflows")
                   for path in paths for part in path.parts)
    assert not any(path.parent.name == "experiments" and path.name == "runner.py" for path in paths)
    assert isinstance(environment()["libzstd"], str)


def test_engine_requirements_are_locked_without_cloud_dependencies() -> None:
    import tomllib

    module = script("prepare_engine_image")
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    lines = module.engine_requirements(lock).splitlines()
    assert {line.split("==")[0] for line in lines} == {
        "pydantic", "pydantic-core", "annotated-types", "typing-extensions",
        "typing-inspection", "zstandard",
    }
    assert all("--hash=sha256:" in line for line in lines)


def test_reviewed_context_rejects_changes_and_extra_files(tmp_path: Path) -> None:
    module = script("create_daytona_snapshot")
    context = tmp_path / "context"
    context.mkdir()
    files = {}
    for name in module.CONTEXT_FILES:
        data = name.encode()
        (context / name).write_bytes(data)
        files[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    (tmp_path / "manifest.json").write_text(json.dumps({"files": files}))
    assert module.reviewed_context(tmp_path) == context
    (context / "unexpected.txt").write_text("synthetic excluded content")
    with pytest.raises(ValueError, match="Unexpected"):
        module.reviewed_context(tmp_path)
    (context / "unexpected.txt").unlink()
    (context / "Dockerfile").write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        module.reviewed_context(tmp_path)


def test_request_file_is_bounded_and_rejects_symlinks(tmp_path: Path) -> None:
    path = tmp_path / "request.json"
    path.write_bytes(b"x" * 65537)
    with pytest.raises(ValueError, match="byte limit"):
        read_json_file(path)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises((ValueError, OSError)):
        read_json_file(link)


def test_image_smoke_roundtrip_and_replay() -> None:
    result = subprocess.run([sys.executable, str(ROOT / "deploy" / "smoke_engine.py")],
                            env={}, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "ok" and report["independent_decode"] is True


@pytest.mark.asyncio
async def test_snapshot_creation_and_no_duplicate_submission(tmp_path: Path, monkeypatch) -> None:
    from daytona.common.errors import DaytonaNotFoundError
    from daytona_api_client.models.snapshot_state import SnapshotState

    from repoweave.config import Settings

    module = script("create_daytona_snapshot")
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim-bookworm\n")
    monkeypatch.setattr(module, "reviewed_context", lambda _: tmp_path)
    monkeypatch.setattr(module, "load_settings", lambda: Settings(DAYTONA_API_KEY="synthetic-key"))
    calls = []
    existing = None

    class Client:
        def __init__(self, config):
            assert config.otel_enabled is False
            self.snapshot = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, name):
            if existing is None:
                raise DaytonaNotFoundError("missing")
            return existing

        async def create(self, params, *, timeout):
            nonlocal existing
            calls.append(params)
            assert timeout == 600
            assert (params.resources.cpu, params.resources.memory, params.resources.disk) == (2, 4, 10)
            assert params.region_id == "us" and params.resources.gpu == 0
            existing = SimpleNamespace(name=params.name, id="synthetic-id", state=SnapshotState.ACTIVE,
                                       cpu=2, mem=4, disk=10, image_name="synthetic-image:1")
            return existing

    monkeypatch.setattr(module, "AsyncDaytona", Client)
    result = await module.create(tmp_path, "test-snapshot")
    assert result["state"] == "active"
    assert json.loads((tmp_path / "snapshot.json").read_text())["name"] == "test-snapshot"
    with pytest.raises(ValueError, match="already exists"):
        await module.create(tmp_path, "test-snapshot")
    assert await module.create(tmp_path, "test-snapshot", status_only=True) == result
    assert len(calls) == 1
