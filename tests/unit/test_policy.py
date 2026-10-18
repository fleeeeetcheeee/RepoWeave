import json
from pathlib import Path

import pytest

from repoweave.archive.manifest import ArchiveFormatError
from repoweave.routing.schemas import Policy, load_policy


def test_committed_baseline_matches_default() -> None:
    assert load_policy(Path(__file__).resolve().parents[2] / "policies" / "baseline.json") == Policy()


@pytest.mark.parametrize("changes", [
    {"schema_version": 2}, {"schema_version": True}, {"codec": "dynamic.module"},
    {"zstd_level": 23}, {"zstd_level": True}, {"dictionary_id": "missing"},
    {"grouping": "graph"}, {"unexpected": "value"},
])
def test_policy_rejects_unsupported_features(tmp_path: Path, changes: dict) -> None:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({**Policy().model_dump(), **changes}))
    with pytest.raises(ArchiveFormatError):
        load_policy(path)


@pytest.mark.parametrize("data", [b"invalid", b"\xff", b" " * 65537])
def test_policy_read_is_bounded_and_reports_no_contents(tmp_path: Path, data: bytes) -> None:
    path = tmp_path / "policy.json"
    path.write_bytes(data)
    with pytest.raises(ArchiveFormatError):
        load_policy(path)
