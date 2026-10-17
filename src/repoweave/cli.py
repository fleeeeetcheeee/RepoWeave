import argparse
import json
import os
import tempfile
from pathlib import Path

from repoweave.config import ConfigurationError, load_settings
from repoweave.repository import RepositoryError, create_snapshot


def _analyze(input_root: Path, output: Path, data_dir: Path) -> None:
    if input_root.is_symlink() or not input_root.is_dir():
        raise RepositoryError("Input must be an existing directory, not a symlink")
    if output.exists() or output.is_symlink():
        raise RepositoryError("Analysis output already exists; choose a new report path")
    output.parent.mkdir(parents=True, exist_ok=True)
    root = input_root.resolve()
    report_parent = output.parent.resolve()
    report_exclusion = (
        report_parent if report_parent != root and report_parent.is_relative_to(root) else output
    )
    snapshot = create_snapshot(
        input_root, data_dir / "snapshots", exclude_paths=(data_dir, report_exclusion),
    )
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent) as temporary:
        json.dump(snapshot.to_report(), temporary, sort_keys=True, indent=2)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        # Publish without replacing an existing file, including one created during capture.
        os.link(temporary.name, output)
    print(f"Snapshot: {snapshot.snapshot_id}\nFiles: {snapshot.files_root}\nReport: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="repoweave",
        description="Repository-aware lossless compression experiments",
    )
    commands = parser.add_subparsers(dest="command")
    analyze = commands.add_parser("analyze", help="Capture and analyze a local repository")
    analyze.add_argument("input", type=Path)
    analyze.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        settings = load_settings()
        if arguments.command == "analyze":
            _analyze(arguments.input, arguments.output, settings.data_dir)
    except (ConfigurationError, RepositoryError) as error:
        parser.exit(2, f"{parser.prog}: {error}\n")
    except OSError:
        parser.exit(2, f"{parser.prog}: Unable to write analysis output\n")


if __name__ == "__main__":
    main()
