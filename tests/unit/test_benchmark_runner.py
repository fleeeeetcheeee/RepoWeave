from pathlib import Path

import pytest

from repoweave.benchmarks import runner
from repoweave.repository import create_snapshot
from repoweave.routing.objective import Limits
from repoweave.routing.schemas import Policy


def test_warmups_discarded_memory_limits_and_late_failures(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "input"
    root.mkdir()
    captured = create_snapshot(root, tmp_path / "snapshots")
    calls = []

    def measure(snapshot, artifact, restored, method, phase, policy, registry, timeout):
        calls.append((method, phase))
        if artifact.name == "candidate-2.archive" and phase == "decode":
            return {"status": "failed", "error": "synthetic-mismatch"}
        return {"status": "ok", "archive_bytes": 100, "seconds": 0.01,
                "peak_rss_bytes": 2000}

    monkeypatch.setattr(runner, "_measure", measure)
    report = runner.benchmark(captured, Policy(), data_dir=tmp_path / "data", repeats=2, warmups=1,
                              limits=Limits(peak_memory_bytes=1000))
    candidate = report["results"][0]
    assert candidate["status"] == "failed" and candidate["failed_phase"] == "decode"
    assert len(candidate["samples"]) == 1
    assert all(result["score_bytes"] is None for result in report["results"])
    assert all(len(result["samples"]) == 2 for result in report["results"][1:])
    assert report["smallest_eligible"] is None
    assert len(calls) == 24
    assert not list((tmp_path / "data").glob("benchmark-*"))


@pytest.mark.parametrize("limits", [{"encode_seconds": float("inf")},
                                   {"decode_seconds": float("nan")},
                                   {"peak_memory_bytes": -1}])
def test_invalid_resource_limits(limits) -> None:
    with pytest.raises(ValueError):
        Limits(**limits)
