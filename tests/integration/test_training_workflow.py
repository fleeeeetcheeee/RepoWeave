import json
import subprocess
import sys
from pathlib import Path

import pytest

from repoweave.archive.decode import unpack
from repoweave.benchmarks.runner import benchmark
from repoweave.benchmarks.splits import Splits
from repoweave.dictionaries.train import load_bundle, train_dictionaries
from repoweave.repository import create_snapshot
from repoweave.routing.encode import encode_policy
from repoweave.routing.schemas import Policy


def repository(root: Path, *, validation: bool = False) -> None:
    root.mkdir()
    for index in range(50):
        body = (f"import module{(index + 1) % 50:02d}\n"
                f"def function_{index}(value):\n    return value + {index + validation}\n")
        (root / f"module{index:02d}.py").write_text(body * (40 if validation else 8))


def test_training_cli_pack_and_independent_unpack(tmp_path: Path) -> None:
    repository(tmp_path / "training")
    repository(tmp_path / "validation", validation=True)
    splits = Splits(train=["training"], validation=["validation"], test=["untouched"])
    split_file = tmp_path / "splits.json"
    split_file.write_text(splits.model_dump_json())
    result = subprocess.run([
        sys.executable, "-m", "repoweave.cli", "train-dictionaries", "--splits", str(split_file),
        "--repository", f"training={tmp_path / 'training'}", "--grouping", "extension",
        "--dictionary-size", "256",
    ], cwd=tmp_path, env={}, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "trained" in result.stdout
    bundle_path, = (tmp_path / ".repoweave/dictionaries").glob("*.json")
    bundle = load_bundle(bundle_path.parent, bundle_path.stem)
    assert bundle.groups[".py"].status == "trained"
    policy = Policy(dictionary_id=bundle.dictionary_id, grouping="extension")
    path = policy.save(tmp_path / ".repoweave/policies")
    result = subprocess.run([
        sys.executable, "-m", "repoweave.cli", "pack", str(tmp_path / "validation"),
        "--policy", str(path), "--output", str(tmp_path / "archive.rpw"),
    ], cwd=tmp_path, env={}, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    bundle_path.unlink()
    manifest = unpack(tmp_path / "archive.rpw", tmp_path / "restored")
    assert manifest.assets
    assert (tmp_path / "restored/module00.py").read_bytes() == (
        tmp_path / "validation/module00.py"
    ).read_bytes()


def test_real_graph_and_extension_control_on_same_snapshot(tmp_path: Path) -> None:
    repository(tmp_path / "training")
    repository(tmp_path / "validation", validation=True)
    training = create_snapshot(tmp_path / "training", tmp_path / "snapshots")
    validation = create_snapshot(tmp_path / "validation", tmp_path / "snapshots")
    splits = Splits(train=["training"], validation=["validation"], test=["untouched"])
    data_dir = tmp_path / "data"
    policies = {}
    for grouping in ("graph", "extension"):
        bundle = train_dictionaries({"training": training}, splits, grouping=grouping,
                                    dictionary_size=256)
        assert all(group.status == "trained" for group in bundle.groups.values())
        bundle.save(data_dir / "dictionaries")
        policies[grouping] = Policy(grouping=grouping, dictionary_id=bundle.dictionary_id)
    with pytest.raises(ValueError, match="control"):
        benchmark(validation, policies["graph"], data_dir=data_dir)
    report = benchmark(validation, policies["graph"], control_policy=policies["extension"],
                       data_dir=data_dir, repeats=2, warmups=1,
                       splits=splits, repository_id="validation")
    assert report["dictionary_fallback_groups"] == {"candidate": [], "extension-control": []}
    for result in report["results"]:
        assert result["status"] == "ok", result
        assert len(result["samples"]) == 2
    assert report["results"][0]["samples"][0]["embedded_dictionary_bytes"] > 0
    # Grouping is an experiment label; no unmeasured improvement is asserted.
    assert report["control_policy"]["grouping"] == "extension"
    assert "return value" not in json.dumps(report)


def test_policy_grouping_mismatch_cannot_write(tmp_path: Path) -> None:
    repository(tmp_path / "training")
    captured = create_snapshot(tmp_path / "training", tmp_path / "snapshots")
    splits = Splits(train=["training"], validation=[], test=[])
    bundle = train_dictionaries({"training": captured}, splits, grouping="extension")
    bundle.save(tmp_path / "registry")
    with pytest.raises(ValueError, match="grouping"):
        encode_policy(captured, tmp_path / "bad.rpw", Policy(dictionary_id=bundle.dictionary_id),
                      tmp_path / "registry")
    assert not (tmp_path / "bad.rpw").exists()
