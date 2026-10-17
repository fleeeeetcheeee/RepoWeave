"""Capture independent files and hash the exact bytes written to the snapshot."""

import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from repoweave.repository.inventory import Exclusion, SourceEntry, iter_inventory
from repoweave.repository.paths import RepositoryError, excluded_relative_paths

SCHEMA_VERSION = 2
SCOPE = "regular-files-directories-executable-v2"


@dataclass(frozen=True)
class Entry:
    path: str
    kind: Literal["file", "directory"]
    size_bytes: int | None = None
    sha256: str | None = None
    executable_bits: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    files_root: Path
    entries: tuple[Entry, ...]
    exclusions: tuple[Exclusion, ...]

    def to_report(self) -> dict[str, object]:
        """Stable metadata only; local storage location is deliberately separate."""
        return {
            "schema_version": SCHEMA_VERSION,
            "scope": SCOPE,
            "snapshot_id": self.snapshot_id,
            "entries": [entry.to_dict() for entry in self.entries],
            "exclusions": [asdict(exclusion) for exclusion in self.exclusions],
            "file_count": sum(entry.kind == "file" for entry in self.entries),
            "total_bytes": sum(entry.size_bytes or 0 for entry in self.entries),
        }


def _copy_file(source: SourceEntry, destination: Path) -> Entry:
    # Nonblocking + no-follow prevents a raced-in FIFO or symlink from being read.
    fd = os.open(
        source.path.rsplit("/", 1)[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
        dir_fd=source.parent_fd,
    )
    with os.fdopen(fd, "rb") as reader:
        before = os.fstat(reader.fileno())
        if not stat.S_ISREG(before.st_mode) or not os.path.samestat(source.info, before):
            raise RepositoryError("A source file changed during capture")
        digest = hashlib.sha256()
        size = 0
        with destination.open("xb") as writer:
            while size < before.st_size:
                chunk = reader.read(min(1024 * 1024, before.st_size - size))
                if not chunk:
                    raise RepositoryError("A source file changed during capture")
                writer.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        after = os.fstat(reader.fileno())
        if (
            size != before.st_size
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
        ):
            raise RepositoryError("A source file changed during capture")
    executable_bits = before.st_mode & 0o111
    destination.chmod(0o400 | executable_bits)
    return Entry(source.path, "file", size, digest.hexdigest(), executable_bits)


def create_snapshot(
    input_root: Path, snapshots_dir: Path, *, exclude_paths: tuple[Path, ...] = (),
) -> Snapshot:
    """Return a completed, run-owned snapshot; never modify an existing capture.

    Consumers must not mutate files_root. Capture is not a filesystem-wide transaction.
    """
    if os.name != "posix":
        raise RepositoryError("Secure snapshot capture currently requires macOS or Linux")
    staging: Path | None = None
    root_fd: int | None = None
    try:
        if input_root.is_symlink() or snapshots_dir.is_symlink():
            raise RepositoryError("Input and snapshot storage roots cannot be symlinks")
        absolute_input = Path(os.path.abspath(input_root))
        root = absolute_input.parent.resolve(strict=True) / absolute_input.name
        store = snapshots_dir.resolve()
        excluded = excluded_relative_paths(root, (store, *exclude_paths))
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        store.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".capture-", dir=store))
        files_root = staging / "files"
        files_root.mkdir()
        entries: list[Entry] = []
        exclusions = [Exclusion(path, "output/data") for path in excluded]
        inventory = iter_inventory(root_fd, excluded)
        try:
            for source in inventory:
                if isinstance(source, Exclusion):
                    exclusions.append(source)
                elif stat.S_ISDIR(source.info.st_mode):
                    (files_root / source.path).mkdir()
                    entries.append(Entry(source.path, "directory"))
                else:
                    entries.append(_copy_file(source, files_root / source.path))
        finally:
            inventory.close()
        ordered_entries = tuple(sorted(entries, key=lambda entry: entry.path))
        manifest = {
            "schema_version": SCHEMA_VERSION, "scope": SCOPE,
            "entries": [entry.to_dict() for entry in ordered_entries],
        }
        canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        snapshot_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        excluded_names = {exclusion.path for exclusion in exclusions}
        exclusions = [
            exclusion for exclusion in exclusions
            if not any(parent.as_posix() in excluded_names for parent in Path(exclusion.path).parents)
        ]
        # shortcut: identical captures use separate storage; add verified reuse if disk use warrants it.
        published = store / f"{snapshot_id}-{staging.name.removeprefix('.capture-')}"
        snapshot = Snapshot(
            snapshot_id, published / "files", ordered_entries,
            tuple(sorted(exclusions, key=lambda exclusion: exclusion.path)),
        )
        (staging / "report.json").write_text(
            json.dumps(snapshot.to_report(), sort_keys=True, indent=2) + "\n", encoding="utf-8",
        )
        staging.rename(published)
        staging = None
        return snapshot
    except (OSError, RuntimeError):
        raise RepositoryError(
            "Unable to capture repository: unreadable or changed input/storage"
        ) from None
    finally:
        if root_fd is not None:
            os.close(root_fd)
        if staging is not None:
            shutil.rmtree(staging)
