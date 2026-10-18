"""Synthetic engine check run as the unprivileged user during the image build."""

import json
import shutil
import sys
import tempfile
from pathlib import Path

from repoweave.archive.decode import unpack
from repoweave.archive.integrity import verify
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
        snapshot = create_snapshot(source, root / "snapshots")
        for method in ("rpw", "tar-zstd", "tar-lzma"):
            job = root / method
            job.mkdir()
            write_archive(snapshot, job / "input.rpw", codec_id="stored")
            request = Request(
                snapshot_id=snapshot.snapshot_id, input_sha256=file_hash(job / "input.rpw"),
                policy=Policy(), method=method, environment_id=content_id(environment()),
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
        decoder = root / "decoder"
        decoder.mkdir()
        shutil.copyfile(root / "rpw" / "archive.bin", decoder / "archive.bin")
        decode_only(decoder)
        unpack(decoder / "restored.rpw", root / "restored")
        verify(source, root / "restored")
        assert not any(name in sys.modules for name in ("daytona", "droid_sdk", "langgraph"))
        print(json.dumps({"status": "ok", "engine_id": engine_id(),
                          "methods": ["rpw", "tar-zstd", "tar-lzma"],
                          "independent_decode": True}))


if __name__ == "__main__":
    main()
