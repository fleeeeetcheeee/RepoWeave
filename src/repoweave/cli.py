import argparse
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from repoweave.archive.manifest import ArchiveFormatError
from repoweave.codecs.base import CodecError
from repoweave.config import ConfigurationError, load_settings
from repoweave.repository import RepositoryError, create_snapshot
from repoweave.repository.inventory import Exclusion


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
    packing = commands.add_parser("pack", help="Capture a repository and write a verified RPW archive")
    packing.add_argument("input", type=Path)
    packing.add_argument("-o", "--output", type=Path, required=True)
    selection = packing.add_mutually_exclusive_group()
    selection.add_argument("--policy", type=Path)
    selection.add_argument("--codec", choices=("stored", "zstd", "lzma"), default="zstd")
    unpacking = commands.add_parser("unpack", help="Verify and restore into a new directory")
    unpacking.add_argument("archive", type=Path)
    unpacking.add_argument("-o", "--output", type=Path, required=True)
    verification = commands.add_parser("verify", help="Compare supported paths, bytes, and executable bits")
    verification.add_argument("original", type=Path)
    verification.add_argument("restored", type=Path)
    verification.add_argument("--exclude", action="append", default=[], metavar="RELATIVE_PATH")
    arguments = parser.parse_args()
    try:
        if arguments.command == "unpack":
            from repoweave.archive.decode import unpack

            manifest = unpack(arguments.archive, arguments.output)
            print(f"Restored {len(manifest.entries)} entries to {arguments.output}")
            return
        settings = load_settings()
        if arguments.command == "analyze":
            _analyze(arguments.input, arguments.output, settings.data_dir)
        elif arguments.command == "pack":
            from repoweave.archive.encode import pack
            from repoweave.routing.schemas import Policy, load_policy

            policy = load_policy(arguments.policy) if arguments.policy else Policy(codec=arguments.codec)
            snapshot = pack(
                arguments.input, arguments.output, data_dir=settings.data_dir,
                codec_id=policy.codec, parameters=policy.parameters,
            )
            print(f"Archive: {arguments.output}\nSnapshot: {snapshot.snapshot_id}")
            _show_exclusions(snapshot.exclusions)
        elif arguments.command == "verify":
            from repoweave.archive.integrity import verify

            original_exclusions, restored_exclusions = verify(
                arguments.original, arguments.restored, data_dir=settings.data_dir,
                exclude_paths=tuple(arguments.exclude),
            )
            print("Verified: supported paths, bytes, and executable bits match")
            _show_exclusions(original_exclusions, "Original")
            _show_exclusions(restored_exclusions, "Restored")
    except (ConfigurationError, RepositoryError, ArchiveFormatError, CodecError) as error:
        parser.exit(2, f"{parser.prog}: {error}\n")
    except OSError:
        parser.exit(2, f"{parser.prog}: Filesystem operation failed; check paths and permissions\n")
    except KeyboardInterrupt:
        parser.exit(130, f"{parser.prog}: Interrupted\n")


def _show_exclusions(exclusions: Iterable[Exclusion], label: str = "Input") -> None:
    for exclusion in exclusions:
        print(f"{label} excluded: {exclusion.path!r} ({exclusion.reason})")


if __name__ == "__main__":
    main()
