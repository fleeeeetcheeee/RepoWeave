"""Capture source bytes once, then encode only the independent snapshot."""

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from repoweave.repository import RepositoryError, Snapshot, create_snapshot

from .filesystem import open_directory, require_absent
from .writer import write_archive

if TYPE_CHECKING:
    from repoweave.routing.schemas import Policy


def pack(
    input_root: Path, output: Path, *, data_dir: Path = Path(".repoweave"),
    codec_id: str = "zstd", parameters: Mapping[str, int] | None = None,
    policy: "Policy | None" = None,
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
    if policy is None:
        write_archive(snapshot, output, codec_id=codec_id, parameters=parameters)
    else:
        from repoweave.routing.encode import encode_policy

        policy.save(data_dir / "policies")
        encode_policy(snapshot, output, policy, data_dir / "dictionaries")
    return snapshot
