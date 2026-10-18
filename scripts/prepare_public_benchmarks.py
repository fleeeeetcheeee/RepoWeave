"""Stage pinned public release archives as data; no downloads or source execution."""

import argparse
import hashlib
import io
import tarfile
from pathlib import Path, PurePosixPath

from repoweave.routing.policy import write_json

RELEASES = {
    "django-5.1.4": (
        "https://codeload.github.com/django/django/tar.gz/refs/tags/5.1.4",
        "adbb3c6702033d417401ce1925f7307b70ee2d8bfca636c82c66a38c55b97dda",
    ),
    "sympy-1.13.3": (
        "https://codeload.github.com/sympy/sympy/tar.gz/refs/tags/sympy-1.13.3",
        "3cfd34ee7393c3786356b8ce49c3d6d212960d4c87c61d47e29a381cb5f41f58",
    ),
    "zstd-1.5.7": (
        "https://codeload.github.com/facebook/zstd/tar.gz/refs/tags/v1.5.7",
        "37d7284556b20954e56e1ca85b80226768902e2edabd3b649e9e72c0c9012ee3",
    ),
}


def prepare(release: str, archive: Path, output: Path) -> None:
    url, digest = RELEASES[release]
    with archive.open("rb") as source:
        data = source.read(64 * 1024 * 1024 + 1)
    if len(data) > 64 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Public release digest or size mismatch")
    output.mkdir(parents=True, exist_ok=False)
    root = output / "source"
    root.mkdir()
    total = 0
    skipped = []
    prefix = None
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as stream:
        for index, member in enumerate(stream):
            path = PurePosixPath(member.name)
            if index >= 20000 or path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError("Unsupported public release layout")
            prefix = prefix or path.parts[0]
            if path.parts[0] != prefix:
                raise ValueError("Multiple release roots")
            relative = path.parts[1:]
            if not relative:
                if not member.isdir():
                    raise ValueError("Release root must be a directory")
                continue
            target = root.joinpath(*relative)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                skipped.append({"path": "/".join(relative), "reason": "non-regular release member"})
                continue
            total += member.size
            if not 0 <= member.size <= 64 * 1024 * 1024 or total > 256 * 1024 * 1024:
                raise ValueError("Public release exceeds extracted byte bounds")
            target.parent.mkdir(parents=True, exist_ok=True)
            reader = stream.extractfile(member)
            if reader is None:
                raise ValueError("Missing release file")
            with reader, target.open("xb") as writer:
                remaining = member.size
                while remaining:
                    chunk = reader.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise ValueError("Truncated public release")
                    writer.write(chunk)
                    remaining -= len(chunk)
            target.chmod(0o600 | (member.mode & 0o111))
    write_json(output / "provenance.json", {"release": release, "url": url, "sha256": digest,
                                            "extracted_file_bytes": total, "skipped": skipped})
    print({"release": release, "extracted_file_bytes": total, "skipped_members": len(skipped)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", choices=RELEASES)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    prepare(args.release, args.archive, args.output)
