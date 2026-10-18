"""Compare metadata and solid-codec stages on one local immutable snapshot."""

import argparse
from pathlib import Path

from repoweave.archive.manifest import HEADER
from repoweave.archive.metadata import PREFIX
from repoweave.archive.reader import read_manifest
from repoweave.benchmarks.runner import benchmark
from repoweave.repository import create_snapshot
from repoweave.routing.encode import encode_policy
from repoweave.routing.objective import Limits
from repoweave.routing.policy import write_json
from repoweave.routing.schemas import Policy


def evaluate(source: Path, output: Path, block_bytes: int = 8 * 1024 * 1024) -> dict:
    policies = [
        ("solid-zstd-json", Policy(solid_block_bytes=block_bytes)),
        ("solid-zstd-compressed", Policy(solid_block_bytes=block_bytes, metadata_encoding="compressed")),
        ("solid-zstd-compact", Policy(solid_block_bytes=block_bytes, metadata_encoding="compact")),
        ("solid-lzma-compact", Policy(codec="lzma", solid_block_bytes=block_bytes,
                                      metadata_encoding="compact")),
    ]
    output.mkdir(parents=True, exist_ok=False)
    snapshot = create_snapshot(source, output / "captures", exclude_paths=(output,))
    rows = []
    for name, policy in policies:
        print(f"Measuring {name}", flush=True)
        report = benchmark(snapshot, policy, data_dir=output / name, repeats=2, warmups=1,
                           limits=Limits(encode_seconds=120., decode_seconds=120.))
        write_json(output / f"{name}.json", report)
        if not all(result["eligible"] for result in report["results"]):
            raise ValueError("Stage or control failed repeated validation; see stage report")
        archive = output / f"{name}.rpw"
        encode_policy(snapshot, archive, policy, output / "dictionaries")
        with archive.open("rb") as stream:
            manifest = read_manifest(stream)
            wire_bytes = stream.tell() - HEADER.size
            stream.seek(0)
            _, version, _ = HEADER.unpack(stream.read(HEADER.size))
            representation, compression, expanded = (
                PREFIX.unpack(stream.read(PREFIX.size)) if version == 4 else (0, 0, wire_bytes)
            )
        candidate = report["results"][0]
        if archive.stat().st_size != candidate["score_bytes"]:
            raise ValueError("Retained archive differs from measured sizes")
        rows.append({
            "stage": name, "policy": policy.model_dump(), "archive_bytes": archive.stat().st_size,
            "format_version": version, "wire_metadata_bytes": wire_bytes,
            "metadata_representation": "compact" if representation else "json",
            "metadata_compression": "zstd" if compression else "stored",
            "decoded_metadata_bytes": expanded, "payload_bytes": manifest.payload_size,
            "block_count": len(manifest.blocks),
            "maximum_block_bytes": max((block.size for block in manifest.blocks), default=0),
            "timing": candidate["timing"], "samples": candidate["samples"],
            "controls": {result["name"]: result["score_bytes"] for result in report["results"][1:]},
        })
        print({"stage": name, "archive_bytes": archive.stat().st_size,
               "controls": rows[-1]["controls"]}, flush=True)
    receipt = {"scope": "exploratory development comparisons, not final holdout evidence",
               "snapshot": snapshot.to_report(), "stages": rows}
    write_json(output / "report.json", receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--block-bytes", type=int, default=8 * 1024 * 1024)
    args = parser.parse_args()
    evaluate(args.source, args.output, args.block_bytes)
