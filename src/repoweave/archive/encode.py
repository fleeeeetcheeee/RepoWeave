"""Capture source bytes once, then encode only the independent snapshot."""

from collections.abc import Mapping
from pathlib import Path

from repoweave.repository import RepositoryError, Snapshot, create_snapshot

from .filesystem import open_directory, require_absent
from .writer import write_archive


def pack(
    input_root: Path, output: Path, *, data_dir: Path = Path(".repoweave"),
    codec_id: str = "zstd", parameters: Mapping[str, int] | None = None,
) -> Snapshot:
    if input_root.is_symlink() or not input_root.is_dir():
        raise RepositoryError("Input must be an existing directory, not a symlink")
    with open_directory(output.parent, create=True) as parent:
        require_absent(parent, output.name)
    root = input_root.resolve()
    parent_path = output.parent.resolve()
    excluded_output = (
        parent_path if parent_path != root and parent_path.is_relative_to(root) else output
    )
    snapshot = create_snapshot(
        input_root, data_dir / "snapshots", exclude_paths=(data_dir, excluded_output),
    )
    write_archive(snapshot, output, codec_id=codec_id, parameters=parameters)
    return snapshot
