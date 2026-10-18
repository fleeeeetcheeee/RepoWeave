"""Create the reviewed engine snapshot in Daytona; no experiment data is uploaded."""

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path

from daytona import AsyncDaytona, CreateSnapshotParams, DaytonaConfig, Image, Resources
from daytona.common.errors import DaytonaNotFoundError

from repoweave.config import load_settings

CONTEXT_FILES = {"Dockerfile", ".dockerignore", "requirements.txt", "smoke_engine.py",
                 "repoweave_engine-0.1.0-py3-none-any.whl"}


def reviewed_context(build: Path) -> Path:
    context = build / "context"
    manifest = json.loads((build / "manifest.json").read_text())
    if {path.name for path in context.iterdir()} != CONTEXT_FILES:
        raise ValueError("Unexpected build-context files")
    if set(manifest["files"]) != CONTEXT_FILES:
        raise ValueError("Unexpected manifest files")
    for path in context.iterdir():
        if path.is_symlink() or not path.is_file():
            raise ValueError("Context must contain regular files only")
        expected = manifest["files"][path.name]
        if (path.stat().st_size != expected["bytes"]
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected["sha256"]):
            raise ValueError("Build context changed since review")
    return context


def summary(snapshot) -> dict:
    return {"name": snapshot.name, "id": snapshot.id, "state": snapshot.state,
            "cpu": snapshot.cpu, "memory_gib": snapshot.mem, "disk_gib": snapshot.disk,
            "image": snapshot.image_name}


async def create(build: Path, name: str, *, status_only: bool = False) -> dict:
    context = reviewed_context(build)
    settings = load_settings()
    if settings.daytona_api_key is None:
        raise ValueError("DAYTONA_API_KEY is required")
    config = DaytonaConfig(api_key=settings.daytona_api_key.get_secret_value(),
                           target="us", otel_enabled=False)
    async with AsyncDaytona(config) as client:
        try:
            async with asyncio.timeout(30):
                existing = await client.snapshot.get(name)
        except DaytonaNotFoundError:
            existing = None
        if status_only:
            return summary(existing) if existing else {"name": name, "state": "not-found"}
        if existing is not None:
            raise ValueError("Snapshot name already exists; inspect it instead of recreating")
        image = Image.from_dockerfile(context / "Dockerfile", strict_context=True)
        snapshot = await client.snapshot.create(
            CreateSnapshotParams(name=name, image=image, region_id="us",
                                 resources=Resources(cpu=2, memory=4, disk=10, gpu=0)),
            timeout=600,
        )
        result = summary(snapshot)
        (build / "snapshot.json").write_text(json.dumps(result, indent=2) + "\n")
        if snapshot.state != "active":
            raise ValueError("Snapshot is not active")
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", type=Path)
    parser.add_argument("--name", required=True, help="Unique versioned engine snapshot name")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    try:
        result = asyncio.run(create(args.build.resolve(), args.name, status_only=args.status))
    except Exception as error:  # noqa: BLE001 - only safe error types cross the CLI boundary
        # SDK exception bodies can contain headers or signed URLs; show only the type.
        code = getattr(error, "code", None)
        message = str(error).lower()
        # Only a short plain-language server message can be displayed, never headers/URLs/tokens.
        safe_message = getattr(error, "message", "")
        key = load_settings().daytona_api_key
        if key is not None:
            safe_message = safe_message.replace(key.get_secret_value(), "[redacted]")
        if (not re.fullmatch(r"[A-Za-z .,:'()\[\]-]{1,300}", safe_message)
                or re.search(r"[A-Za-z]{24,}", safe_message)):
            safe_message = None
        print(json.dumps({
            "status": "failed", "error_type": type(error).__name__,
            "http_status": getattr(error, "status_code", None),
            "message": safe_message,
            "code": code if isinstance(code, str) and re.fullmatch(r"[A-Z_]{1,80}", code) else None,
            "reason_tags": [word for word in ("permission", "scope", "quota", "limit", "balance",
                                              "billing", "snapshot", "object storage", "credits")
                            if word in message],
        }))
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2))
