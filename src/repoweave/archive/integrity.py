"""Byte integrity and comparison of the declared repository scope."""

import hashlib
import tempfile
from pathlib import Path

from repoweave.repository import create_snapshot
from repoweave.repository.inventory import Exclusion

from .filesystem import open_regular
from .manifest import ArchiveFormatError


class VerificationError(ArchiveFormatError):
    """Supported repository contents differ."""


def check_bytes(data: bytes, size: int, sha256: str) -> None:
    if len(data) != size or hashlib.sha256(data).hexdigest() != sha256:
        raise ArchiveFormatError("File size or SHA-256 does not match the manifest")


def verify(
    original: Path, restored: Path, *, exclude_paths: tuple[str, ...] = (),
    data_dir: Path | None = None,
) -> tuple[tuple[Exclusion, ...], tuple[Exclusion, ...]]:
    """Compare supported paths, actual bytes, and executable bits using fresh captures.

    Relative exclusions apply to both roots; default inventory exclusions also apply.
    """
    from repoweave.repository.paths import validate_relative_path

    for path in exclude_paths:
        validate_relative_path(path)
    with tempfile.TemporaryDirectory(prefix="repoweave-verify-") as temporary:
        snapshots = []
        for root in (original, restored):
            exclusions = tuple(root / path for path in exclude_paths)
            if data_dir is not None and data_dir.resolve().is_relative_to(root.resolve()):
                exclusions += (data_dir,)
            if (
                root == original and restored.resolve().is_relative_to(original.resolve())
                and restored.resolve() != original.resolve()
            ):
                exclusions += (restored,)
            snapshots.append(create_snapshot(root, Path(temporary), exclude_paths=exclusions))
        left, right = snapshots
        if left.entries != right.entries:
            raise VerificationError("Repository inventories, bytes, or executable bits differ")
        for entry in left.entries:
            if entry.kind != "file":
                continue
            with (
                open_regular(left.files_root / entry.path) as first,
                open_regular(right.files_root / entry.path) as second,
            ):
                while True:
                    a, b = first.read(1024 * 1024), second.read(1024 * 1024)
                    if a != b:
                        raise VerificationError("Repository file bytes differ")
                    if not a:
                        break
        return left.exclusions, right.exclusions
