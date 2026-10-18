import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from repoweave.archive.manifest import ArchiveFormatError
from repoweave.benchmarks.splits import Splits
from repoweave.dictionaries.train import load_bundle, train_dictionaries
from repoweave.repository import create_snapshot
from repoweave.routing.schemas import Policy, load_policy


@pytest.mark.parametrize("values", [
    {"train": ["same"], "validation": ["same"], "test": []},
    {"train": ["same"], "validation": [], "test": ["same"]},
    {"train": [], "validation": ["same"], "test": ["same"]},
    {"train": ["same", "same"], "validation": [], "test": []},
])
def test_partitions_reject_overlap(values) -> None:
    with pytest.raises(ValidationError):
        Splits(**values)


def test_training_requires_training_partition_and_reports_small_sample_fallback(tmp_path: Path) -> None:
    root = tmp_path / "input"
    root.mkdir()
    (root / "tiny.py").write_bytes(b"print('tiny')")
    snapshot = create_snapshot(root, tmp_path / "snapshots")
    splits = Splits(train=["training"], validation=["validation"], test=["heldout"])
    with pytest.raises(ValueError):
        train_dictionaries({"heldout": snapshot}, splits, grouping="none")
    bundle = train_dictionaries({"training": snapshot}, splits, grouping="extension")
    assert bundle.groups[".py"].status == "training-failed"
    assert bundle.for_snapshot(snapshot) == ({}, [".py"])
    path = bundle.save(tmp_path / "dictionaries")
    assert load_bundle(path.parent, bundle.dictionary_id) == bundle
    assert bundle.save(path.parent) == path
    sample = bundle.groups[".py"].samples[0]
    assert sample.snapshot_id == snapshot.snapshot_id
    assert sample.sha256 == hashlib.sha256(b"print('tiny')").hexdigest()
    value = json.loads(path.read_text())
    value["dictionary_size"] += 1
    path.write_text(json.dumps(value))
    with pytest.raises(ArchiveFormatError):
        load_bundle(path.parent, bundle.dictionary_id)


def test_policy_content_identity_and_immutable_versions(tmp_path: Path) -> None:
    original = Policy()
    path = original.save(tmp_path)
    assert path.name == original.policy_id + ".json"
    assert load_policy(path) == original
    assert original.save(tmp_path) == path
    changed = Policy(zstd_level=9)
    assert changed.policy_id != original.policy_id
    changed.save(tmp_path)
    assert load_policy(path) == original
    path.write_text(changed.model_dump_json())
    with pytest.raises(ArchiveFormatError):
        original.save(tmp_path)


def test_retired_holdout_cannot_be_reused() -> None:
    splits = Splits(train=[], validation=[], test=["heldout"], test_retired=True)
    with pytest.raises(ValueError):
        splits.require("heldout", "test")
