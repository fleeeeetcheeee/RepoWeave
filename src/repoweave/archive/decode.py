"""Restore a fully verified archive into a new destination."""

import os
from pathlib import Path

from .filesystem import open_directory, open_regular, publish, require_absent, staging_directory
from .manifest import ArchiveFormatError, DirectoryEntry, Manifest
from .reader import decode_file, read_manifest


def unpack(archive: Path, output: Path) -> Manifest:
    with open_regular(archive) as source:
        manifest = read_manifest(source)
        with open_directory(output.parent, create=True) as parent:
            require_absent(parent, output.name)
            with staging_directory(parent) as (name, staging):
                for entry in manifest.entries:
                    path = Path(entry.path)
                    with open_directory(path.parent, base_fd=staging) as directory:
                        if isinstance(entry, DirectoryEntry):
                            os.mkdir(path.name, mode=0o700, dir_fd=directory)
                        else:
                            data = decode_file(source, entry, manifest.assets)
                            fd = os.open(
                                path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                0o600, dir_fd=directory,
                            )
                            with os.fdopen(fd, "wb") as destination:
                                destination.write(data)
                                destination.flush()
                                os.fchmod(destination.fileno(), 0o600 | entry.executable_bits)
                                os.fsync(destination.fileno())
                if source.read(1):
                    raise ArchiveFormatError("Unexpected trailing archive data")
                publish(parent, name, parent, output.name)
        return manifest
