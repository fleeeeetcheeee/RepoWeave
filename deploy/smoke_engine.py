"""Synthetic engine check run as the unprivileged user during the image build."""

import json
import random
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Literal

from repoweave.archive.decode import unpack
from repoweave.archive.integrity import verify
from repoweave.archive.manifest import HEADER
from repoweave.archive.reader import read_manifest
from repoweave.archive.writer import write_archive
from repoweave.execution.experiment_entrypoint import decode_only, execute
from repoweave.experiments.schemas import Request, Result, engine_id, environment, file_hash
from repoweave.repository import create_snapshot
from repoweave.routing.objective import Limits
from repoweave.routing.policy import content_id, write_json
from repoweave.routing.schemas import Policy


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="repoweave-smoke-") as temporary:
        root = Path(temporary).resolve()
        source = root / "source"
        (source / "empty").mkdir(parents=True)
        (source / "fixture.txt").write_bytes(b"owned synthetic snapshot-build fixture\r\n" * 1000)
        (source / "binary").write_bytes(bytes(range(256)) * 8)
        (source / "binary").chmod(0o751)
        shared = random.Random(4).randbytes(4096)
        for index in range(16):
            (source / f"shared-{index:02d}.bin").write_bytes(shared + bytes([index]))
        snapshot = create_snapshot(source, root / "snapshots")
        methods: tuple[Literal["rpw", "tar-zstd", "tar-lzma"], ...] = ("rpw", "tar-zstd", "tar-lzma")
        cases: list[tuple[str, Literal["rpw", "tar-zstd", "tar-lzma"], Policy]] = [
            (method, method, Policy()) for method in methods]
        modes: tuple[tuple[Literal["zstd", "lzma"], Literal["compressed", "compact"]], ...] = (
            ("zstd", "compressed"), ("zstd", "compact"), ("lzma", "compact"))
        cases += [(f"solid-{codec}-{metadata}", "rpw",
                   Policy(codec=codec, metadata_encoding=metadata, solid_block_bytes=8 * 1024 * 1024))
                  for codec, metadata in modes]
        formats = []
        for name, method, policy in cases:
            job = root / name
            job.mkdir()
            write_archive(snapshot, job / "input.rpw", codec_id="stored")
            request = Request(
                snapshot_id=snapshot.snapshot_id, input_sha256=file_hash(job / "input.rpw"),
                policy=policy, method=method, environment_id=content_id(environment()),
                engine_id=engine_id(), repetition=0,
                limits=Limits(encode_seconds=30.0, decode_seconds=30.0),
            )
            write_json(job / "request.json", request.model_dump())
            execute(job)
            before = (job / "result.json").read_bytes()
            execute(job)
            assert before == (job / "result.json").read_bytes()
            result = Result.model_validate_json(before)
            assert result.job_id == request.job_id and result.sample.reconstructed
            assert file_hash(job / "archive.bin") == result.artifact_sha256
            if method == "rpw":
                with (job / "archive.bin").open("rb") as stream:
                    version = HEADER.unpack(stream.read(HEADER.size))[1]
                    stream.seek(0)
                    manifest = read_manifest(stream)
                if policy.solid_block_bytes:
                    assert version == 4 and any(b.codec == policy.codec for b in manifest.blocks)
                    formats.append(name)
                decoder = root / f"decoder-{name}"
                decoder.mkdir()
                shutil.copyfile(job / "archive.bin", decoder / "archive.bin")
                decode_only(decoder)
                unpack(decoder / "restored.rpw", decoder / "verified")
                verify(source, decoder / "verified")
        assert not any(name in sys.modules for name in ("daytona", "droid_sdk", "langgraph"))
        print(json.dumps({"status": "ok", "engine_id": engine_id(),
                          "methods": ["rpw", "tar-zstd", "tar-lzma"],
                          "v4_formats": formats,
                          "independent_decode": True}))


if __name__ == "__main__":
    main()
