import hashlib
import io
import tarfile

import pytest
from test_engine_image import script


def release(tmp_path, name="release/file", kind=tarfile.REGTYPE):
    archive = tmp_path / "public.tar.gz"
    with tarfile.open(archive, "w:gz") as writer:
        info = tarfile.TarInfo(name)
        info.type = kind
        info.mode = 0o755
        info.size = 3 if kind == tarfile.REGTYPE else 0
        if kind == tarfile.SYMTYPE:
            info.linkname = "../../outside"
        writer.addfile(info, io.BytesIO(b"abc") if info.size else None)
    module = script("prepare_public_benchmarks")
    module.RELEASES["fixture"] = ("https://example.invalid", hashlib.sha256(archive.read_bytes()).hexdigest())
    return module, archive


def test_public_staging_copies_bytes_modes_and_refuses_reuse(tmp_path):
    module, archive = release(tmp_path)
    output = tmp_path / "out"
    module.prepare("fixture", archive, output)
    assert (output / "source/file").read_bytes() == b"abc"
    assert (output / "source/file").stat().st_mode & 0o111 == 0o111
    assert (output / "provenance.json").is_file()
    with pytest.raises(FileExistsError):
        module.prepare("fixture", archive, output)


def test_public_staging_rejects_digest_before_output(tmp_path):
    module, archive = release(tmp_path)
    module.RELEASES["fixture"] = ("https://example.invalid", "0" * 64)
    with pytest.raises(ValueError, match="digest"):
        module.prepare("fixture", archive, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("name", ["../escape", "/outside", "release/../../outside"])
def test_public_staging_rejects_traversal(tmp_path, name):
    module, archive = release(tmp_path, name)
    with pytest.raises(ValueError, match="layout"):
        module.prepare("fixture", archive, tmp_path / "out")
    assert not (tmp_path / "outside").exists()


def test_public_staging_skips_symlinks(tmp_path):
    module, archive = release(tmp_path, kind=tarfile.SYMTYPE)
    module.prepare("fixture", archive, tmp_path / "out")
    assert not (tmp_path / "out/source/file").exists()
