"""Content-addressed, exclusively published JSON artifacts."""

import hashlib
import json
import os
from pathlib import Path

from repoweave.archive.filesystem import open_directory, publish, require_absent, staging_directory


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def content_id(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def write_json(output: Path, value: object) -> None:
    data = canonical(value) + b"\n"
    with open_directory(output.parent, create=True) as parent:
        require_absent(parent, output.name)
        with staging_directory(parent) as (_, staging):
            fd = os.open("report", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=staging)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            publish(staging, "report", parent, output.name)
