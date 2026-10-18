"""Descriptor-relative IO and exclusive publication on macOS/Linux."""

import ctypes
import os
import shutil
import stat
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from .manifest import ArchiveFormatError


@contextmanager
def open_directory(path: Path, *, base_fd: int | None = None, create: bool = False) -> Iterator[int]:
    if os.name != "posix":
        raise ArchiveFormatError("Archive filesystem operations require macOS or Linux")
    if base_fd is None:
        path = Path(os.path.abspath(path))
        fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parts = path.parts[1:]
    else:
        if path.is_absolute() or ".." in path.parts:
            raise ArchiveFormatError("Unsafe relative directory")
        fd = os.dup(base_fd)
        parts = path.parts
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


@contextmanager
def open_regular(path: Path, *, base_fd: int | None = None) -> Iterator[BinaryIO]:
    with open_directory(path.parent, base_fd=base_fd) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ArchiveFormatError("Expected a regular file")
            yield stream


def require_absent(parent_fd: int, name: str) -> None:
    if not name or name in (".", ".."):
        raise ArchiveFormatError("Invalid output name")
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise ArchiveFormatError("Output already exists; choose a new destination")


@contextmanager
def staging_directory(parent_fd: int) -> Iterator[tuple[str, int]]:
    name = f".repoweave-{uuid.uuid4().hex}"
    os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        yield name, fd
    finally:
        current: os.stat_result | None
        try:
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        try:
            # Never remove a replacement directory belonging to another operation.
            if current is not None and os.path.samestat(current, os.fstat(fd)):
                shutil.rmtree(name, dir_fd=parent_fd)
        finally:
            os.close(fd)


def publish(source_fd: int, source: str, destination_fd: int, destination: str) -> None:
    """Atomically rename without replacing files, directories, or dangling symlinks."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        symbol, flag = "renameatx_np", 0x00000004  # RENAME_EXCL
    elif sys.platform == "linux":
        symbol, flag = "renameat2", 1  # RENAME_NOREPLACE
    else:
        raise ArchiveFormatError("Exclusive archive publication is unsupported on this platform")
    rename = getattr(libc, symbol, None)
    if rename is None:
        raise ArchiveFormatError("Exclusive archive publication is unavailable")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(source_fd, os.fsencode(source), destination_fd, os.fsencode(destination), flag):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
