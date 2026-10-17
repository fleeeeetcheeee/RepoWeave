"""Stable, descriptor-relative traversal; repository contents are never executed."""

import os
import stat
from collections.abc import Generator
from dataclasses import dataclass
from fnmatch import fnmatchcase

from repoweave.repository.paths import RepositoryError, validate_relative_path

IGNORED_DIRECTORIES = frozenset({
    ".git", ".venv", "venv", "node_modules", "build", "dist", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".repoweave", "data", "datasets", "artifacts",
})
SECRET_PATTERNS = (
    ".env", ".env.*", ".aws", ".ssh", ".gnupg", ".netrc", ".npmrc", ".pypirc",
    "credentials", "credentials.*", "secrets", "secrets.*", "id_rsa*", "id_ed25519*",
    "id_ecdsa*", "id_dsa*", "*.pem", "*.key", "*.p12", "*.pfx",
)


@dataclass(frozen=True)
class Exclusion:
    path: str
    reason: str


@dataclass(frozen=True)
class SourceEntry:
    path: str
    info: os.stat_result
    parent_fd: int


def iter_inventory(
    directory_fd: int, excluded: tuple[str, ...], prefix: str = "",
) -> Generator[SourceEntry | Exclusion, None, None]:
    """Entry descriptors are valid only until the iterator advances."""
    with os.scandir(directory_fd) as directory:
        names = sorted(entry.name for entry in directory)
    for name in names:
        path = validate_relative_path(f"{prefix}/{name}" if prefix else name)
        if path in excluded:
            continue
        if any(fnmatchcase(name.lower(), pattern) for pattern in SECRET_PATTERNS):
            yield Exclusion(path, "secret-name")
            continue
        if name.lower() in IGNORED_DIRECTORIES:
            yield Exclusion(path, "repository-metadata/build/data")
            continue
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            yield Exclusion(path, "symlink")
        elif stat.S_ISDIR(info.st_mode):
            child_fd = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd,
            )
            try:
                if not os.path.samestat(info, os.fstat(child_fd)):
                    raise RepositoryError("A source directory changed during capture")
                yield SourceEntry(path, info, directory_fd)
                yield from iter_inventory(child_fd, excluded, path)
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(info.st_mode):
            yield SourceEntry(path, info, directory_fd)
        else:
            yield Exclusion(path, "special-file")
