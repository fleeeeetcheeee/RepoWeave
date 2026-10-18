"""Run a single fixed experiment from an owned job directory, without service SDKs."""

import argparse
import fcntl
import json
import tempfile
from pathlib import Path

from repoweave.archive.decode import unpack
from repoweave.archive.filesystem import open_regular
from repoweave.benchmarks.runner import _measure
from repoweave.experiments.schemas import Request, Result, Sample, engine_id, environment, file_hash
from repoweave.repository import create_snapshot
from repoweave.routing.policy import write_json


def read_json_file(path: Path) -> bytes:
    with open_regular(path) as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("Request or result exceeds byte limit")
    return data


def execute(work: Path) -> None:
    with (work / "job.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        request = Request.model_validate_json(read_json_file(work / "request.json"))
        if request.engine_id != engine_id() or file_hash(work / "input.rpw") != request.input_sha256:
            raise ValueError("Engine or input identity differs")
        if (work / "result.json").exists():
            result = Result.model_validate_json(read_json_file(work / "result.json"))
            if (result.job_id != request.job_id
                    or file_hash(work / "archive.bin") != result.artifact_sha256):
                raise ValueError("Existing result differs")
            return
        # An unfinished artifact cannot be mistaken for a completed/replayable job.
        if (work / "archive.bin").exists():
            raise ValueError("Interrupted experiment requires a new run")
        with tempfile.TemporaryDirectory(prefix="measure-", dir=work) as temporary:
            scratch = Path(temporary)
            unpack(work / "input.rpw", scratch / "input")
            snapshot = create_snapshot(scratch / "input", scratch / "snapshots")
            if snapshot.snapshot_id != request.snapshot_id:
                raise ValueError("Snapshot identity differs")
            artifact = work / "archive.bin"
            restored = scratch / "restored"
            encoded = _measure(snapshot, artifact, restored, request.method, "encode",
                               request.policy, work / "dictionaries", request.limits.encode_seconds)
            if encoded["status"] != "ok":
                raise ValueError("Encoding failed")
            decoded = _measure(snapshot, artifact, restored, request.method, "decode",
                               request.policy, work / "dictionaries", request.limits.decode_seconds)
            if decoded["status"] != "ok":
                raise ValueError("Reconstruction failed")
            sample = Sample(
                archive_bytes=encoded["archive_bytes"], reconstructed=True,
                encode_seconds=encoded["seconds"], decode_seconds=decoded["seconds"],
                encode_peak_rss_bytes=encoded["peak_rss_bytes"],
                decode_peak_rss_bytes=decoded["peak_rss_bytes"],
                manifest_bytes=encoded.get("manifest_bytes"), payload_bytes=encoded.get("payload_bytes"),
                embedded_dictionary_bytes=encoded.get("embedded_dictionary_bytes", 0),
            )
            result = Result(job_id=request.job_id, snapshot_id=request.snapshot_id,
                            policy_id=request.policy.policy_id, engine_id=request.engine_id,
                            environment_id=request.environment_id, sample=sample,
                            artifact_sha256=file_hash(artifact), environment=environment())
            write_json(work / "result.json", result.model_dump())


def decode_only(work: Path) -> None:
    """Independent decoder receives only the portable archive and expected identity."""
    from repoweave.archive.writer import write_archive

    unpack(work / "archive.bin", work / "restored")
    snapshot = create_snapshot(work / "restored", work / "snapshots")
    write_archive(snapshot, work / "restored.rpw", codec_id="stored")
    write_json(work / "decoded.json", {"snapshot_id": snapshot.snapshot_id,
                                      "archive_sha256": file_hash(work / "restored.rpw"),
                                      "engine_id": engine_id()})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("measure", "decode"))
    parser.add_argument("work", type=Path)
    args = parser.parse_args()
    try:
        (execute if args.operation == "measure" else decode_only)(args.work)
    except Exception:  # noqa: BLE001 - untrusted engine errors must not expose paths/source
        print(json.dumps({"status": "failed", "error": "engine-operation-failed"}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
