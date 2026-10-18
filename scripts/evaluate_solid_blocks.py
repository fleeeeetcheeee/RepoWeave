"""Local exploratory solid-block sweep with same-level RPW and whole-tree controls."""

import argparse
import tempfile
from pathlib import Path

from repoweave.archive.decode import unpack
from repoweave.archive.manifest import HEADER
from repoweave.benchmarks.runner import benchmark
from repoweave.benchmarks.worker import _compare
from repoweave.repository import create_snapshot
from repoweave.routing.encode import encode_policy
from repoweave.routing.objective import Limits
from repoweave.routing.policy import write_json
from repoweave.routing.schemas import Policy


def evaluate(source: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    snapshot = create_snapshot(source, output / "captures", exclude_paths=(output,))
    rows: list[dict] = []
    for target in (None, 64 * 1024, 1024 * 1024, 8 * 1024 * 1024):
        policy = Policy(solid_block_bytes=target)
        with tempfile.TemporaryDirectory(dir=output) as work:
            archive = Path(work) / "candidate.rpw"
            manifest = encode_policy(snapshot, archive, policy, output / "dictionaries")
            restored = Path(work) / "restored"
            unpack(archive, restored)
            _compare(snapshot, restored)
            with archive.open("rb") as stream:
                version = HEADER.unpack(stream.read(HEADER.size))[1]
            rows.append({
                "policy": policy.model_dump(), "archive_bytes": archive.stat().st_size,
                "block_count": len(manifest.blocks),
                "maximum_block_bytes": max((block.size for block in manifest.blocks), default=0),
                "format_version": version, "reconstructed": True,
            })
    winner = min(rows, key=lambda row: row["archive_bytes"])
    policy = Policy.model_validate(winner["policy"])
    report = benchmark(snapshot, policy, data_dir=output, repeats=2, warmups=1,
                       limits=Limits(encode_seconds=120., decode_seconds=120.))
    candidate = report["results"][0]
    if not candidate["eligible"] or candidate["score_bytes"] != winner["archive_bytes"]:
        raise ValueError("Selected policy failed repeated validation")
    encode_policy(snapshot, output / "selected.rpw", policy, output / "dictionaries")
    receipt = {
        "scope": "exploratory development data; not a final holdout or general superiority claim",
        "snapshot_id": snapshot.snapshot_id, "sweep": rows, "benchmark": report,
    }
    write_json(output / "report.json", receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = evaluate(args.source, args.output)
    print({"report": str(args.output / "report.json"),
           "sizes": [row["archive_bytes"] for row in result["sweep"]],
           "winner": result["benchmark"]["smallest_eligible"]})
