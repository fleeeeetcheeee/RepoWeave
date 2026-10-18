"""Build an allowlisted engine wheel and reviewable Daytona context, without uploading."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

from repoweave.archive.filesystem import open_regular
from repoweave.experiments.schemas import engine_id, engine_sources


def engine_requirements(lock: dict) -> str:
    packages = {package["name"]: package for package in lock["package"]}
    selected: set[str] = set()
    pending = ["pydantic", "zstandard"]
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected.add(name)
        pending.extend(dependency["name"] for dependency in packages[name].get("dependencies", []))
    lines = []
    for name in sorted(selected):
        package = packages[name]
        if package["source"] != {"registry": "https://pypi.org/simple"}:
            raise ValueError("Engine dependencies must come from the public PyPI registry")
        hashes = sorted({wheel["hash"] for wheel in package["wheels"]})
        if not hashes:
            raise ValueError("Engine dependency has no locked wheels")
        lines.append(f'{name}=={package["version"]} ' + " ".join(f"--hash={h}" for h in hashes))
    return "\n".join(lines) + "\n"


def prepare(root: Path, destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=False)
    project = destination / "project"
    context = destination / "context"
    context.mkdir()
    source_root = root / "src" / "repoweave"
    source_names = []
    for path in engine_sources(source_root):
        relative = path.relative_to(source_root)
        target = project / "src" / "repoweave" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with open_regular(path) as stream:
            target.write_bytes(stream.read())
        source_names.append("repoweave/" + relative.as_posix())
    (project / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
        '[project]\nname = "repoweave-engine"\nversion = "0.1.0"\n'
        'requires-python = ">=3.12"\ndependencies = ["pydantic>=2,<3", "zstandard>=0.23,<1"]\n'
        '[tool.hatch.build.targets.wheel]\npackages = ["src/repoweave"]\n'
    )
    requirements = engine_requirements(tomllib.loads((root / "uv.lock").read_text()))
    (context / "requirements.txt").write_text(requirements)
    for source, target_name in (("deploy/experiment.Dockerfile", "Dockerfile"),
                           ("deploy/smoke_engine.py", "smoke_engine.py"),
                           (".dockerignore", ".dockerignore")):
        shutil.copyfile(root / source, context / target_name)
    wheels = destination / "wheels"
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(wheels.resolve()),
                    str(project.resolve())], check=True)
    wheel = context / "repoweave_engine-0.1.0-py3-none-any.whl"
    shutil.copyfile(wheels / wheel.name, wheel)
    with zipfile.ZipFile(wheel) as archive:
        actual = sorted(name for name in archive.namelist() if not name.startswith(
            "repoweave_engine-0.1.0.dist-info/"))
        if actual != sorted(source_names):
            raise ValueError("Wheel contains unexpected files")
    manifest = {"engine_id": engine_id(), "source_files": sorted(source_names),
                "files": {path.name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                      "bytes": path.stat().st_size}
                          for path in sorted(context.iterdir())}}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    manifest = prepare(Path(__file__).resolve().parents[1], args.destination)
    print(json.dumps({"engine_id": manifest["engine_id"], "files": manifest["files"]}, indent=2))
