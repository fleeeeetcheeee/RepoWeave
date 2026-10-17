import hashlib
import json
import os
from pathlib import Path

import pytest

from repoweave.repository import RepositoryError, create_snapshot
from repoweave.repository import snapshot as snapshot_module
from repoweave.repository.paths import validate_relative_path


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    (root / "empty-directory").mkdir()
    files = {
        "a.py": b"raise RuntimeError('This fixture must never execute')\n",
        "b.py": b"raise RuntimeError('This fixture must never execute')\n",
        "package/module.py": b"# Nested synthetic source\n",
        "README.md": b"# Owned fixture\r\nRepeated text.\r\n",
        "settings.json": b'{"example": true}\n',
        "bytes.bin": bytes(range(256)),
        "empty": b"",
        "caf\u00e9.txt": "Hello, \uc138\uacc4!\n".encode(),
    }
    for path, content in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(content)
    (root / "a.py").chmod(0o755)
    return root


def test_exact_stable_capture_and_independence(repository: Path, tmp_path: Path) -> None:
    store = tmp_path / "snapshots"
    first = create_snapshot(repository, store)
    second = create_snapshot(repository, store)
    assert first.snapshot_id == second.snapshot_id
    assert first.files_root != second.files_root
    assert first.to_report() == second.to_report()
    assert [entry.path for entry in first.entries] == sorted(entry.path for entry in first.entries)
    assert (first.files_root / "empty-directory").is_dir()
    for entry in first.entries:
        if entry.kind == "file":
            original = repository / entry.path
            captured = first.files_root / entry.path
            assert captured.read_bytes() == original.read_bytes()
            assert entry.size_bytes == len(captured.read_bytes())
            assert entry.sha256 == hashlib.sha256(captured.read_bytes()).hexdigest()
            assert captured.stat().st_ino != original.stat().st_ino
            assert not captured.stat().st_mode & 0o222
            assert bool(captured.stat().st_mode & 0o111) == entry.executable
    report = first.to_report()
    assert report["file_count"] == 8
    assert "This fixture must never execute" not in json.dumps(report)
    assert json.loads((first.files_root.parent / "report.json").read_text()) == report
    (repository / "a.py").write_bytes(b"changed")
    (repository / "bytes.bin").unlink()
    assert (first.files_root / "a.py").read_bytes().startswith(b"raise RuntimeError")
    assert (first.files_root / "bytes.bin").read_bytes() == bytes(range(256))
    assert create_snapshot(repository, store).snapshot_id != first.snapshot_id


def test_identity_includes_paths_directories_and_executable_status(
    repository: Path, tmp_path: Path,
) -> None:
    store = tmp_path / "snapshots"
    identities = [create_snapshot(repository, store).snapshot_id]
    (repository / "empty").chmod(0o755)
    identities.append(create_snapshot(repository, store).snapshot_id)
    (repository / "empty").rename(repository / "renamed")
    identities.append(create_snapshot(repository, store).snapshot_id)
    (repository / "another-empty-directory").mkdir()
    identities.append(create_snapshot(repository, store).snapshot_id)
    assert len(set(identities)) == 4


def test_exclusions_are_visible_and_never_opened(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (".git", "node_modules", "build", ".aws", "data", "custom-output"):
        (repository / name).mkdir()
        (repository / name / "must-not-read").write_bytes(b"synthetic excluded content")
    for name in (".env", ".env.local", "credentials.json", "private.key", ".npmrc"):
        (repository / name).write_bytes(b"synthetic excluded content")
    (repository / "file-link").symlink_to(tmp_path / "outside")
    (repository / "directory-link").symlink_to(repository, target_is_directory=True)
    os.mkfifo(repository / "pipe")
    original_open = os.open

    def guarded_open(path, flags, mode=0o777, *, dir_fd=None):
        assert Path(path).name not in {"must-not-read", ".env", ".env.local", "private.key"}
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", guarded_open)
    first = create_snapshot(
        repository, repository / "custom-data" / "snapshots",
        exclude_paths=(repository / "custom-data", repository / "custom-output"),
    )
    excluded = {entry.path: entry.reason for entry in first.exclusions}
    assert set(excluded) == {
        ".git", "node_modules", "build", ".aws", "data", "custom-output", "custom-data",
        ".env", ".env.local", "credentials.json", "private.key", ".npmrc",
        "file-link", "directory-link", "pipe",
    }
    assert excluded["file-link"] == excluded["directory-link"] == "symlink"
    assert excluded["pipe"] == "special-file"
    (repository / ".env").write_bytes(b"different synthetic content")
    second = create_snapshot(
        repository, repository / "custom-data" / "snapshots",
        exclude_paths=(repository / "custom-data", repository / "custom-output"),
    )
    assert first.to_report() == second.to_report()


@pytest.mark.parametrize("path", [
    "", ".", "..", "../escape", "a/../escape", "/absolute", "a//b", "a/./b",
    "C:/escape", "C:escape", "a\\b", "\\\\server\\share", "a\x00b", "a\nb", "a\udcff",
])
def test_unsafe_paths_are_rejected(path: str) -> None:
    with pytest.raises(RepositoryError):
        validate_relative_path(path)


def test_unsafe_filename_aborts_and_cleans_capture(repository: Path, tmp_path: Path) -> None:
    (repository / "C:escape").write_bytes(b"fixture")
    store = tmp_path / "snapshots"
    with pytest.raises(RepositoryError, match="Unsafe relative path"):
        create_snapshot(repository, store)
    assert list(store.iterdir()) == []


@pytest.mark.parametrize("failure", ["unreadable", "symlink", "fifo", "write"])
def test_failed_capture_preserves_existing_snapshot_and_cleans_partial(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    store = tmp_path / "snapshots"
    existing = create_snapshot(repository, store)
    previous = set(store.iterdir())
    original_open = os.open
    original_path_open = Path.open
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside synthetic bytes")

    def raced_open(path, flags, mode=0o777, *, dir_fd=None):
        if path == "a.py" and dir_fd is not None:
            if failure == "unreadable":
                raise PermissionError("synthetic failure")
            if failure in {"symlink", "fifo"}:
                (repository / "a.py").unlink()
                if failure == "symlink":
                    (repository / "a.py").symlink_to(outside)
                else:
                    os.mkfifo(repository / "a.py")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    def failed_write(path, *args, **kwargs):
        if failure == "write" and path.name == "a.py" and "xb" in args:
            raise OSError("synthetic disk failure")
        return original_path_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", raced_open)
    monkeypatch.setattr(Path, "open", failed_write)
    with pytest.raises(RepositoryError):
        create_snapshot(repository, store)
    assert set(store.iterdir()) == previous
    assert (existing.files_root / "a.py").read_bytes().startswith(b"raise RuntimeError")
    assert outside.read_bytes() == b"outside synthetic bytes"


def test_directory_symlink_swap_is_not_followed(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (repository / "nested").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private").write_bytes(b"synthetic")
    original_open = os.open

    def raced_open(path, flags, mode=0o777, *, dir_fd=None):
        if path == "nested" and dir_fd is not None:
            (repository / "nested").rmdir()
            (repository / "nested").symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", raced_open)
    store = tmp_path / "snapshots"
    with pytest.raises(RepositoryError):
        create_snapshot(repository, store)
    assert list(store.iterdir()) == []


def test_file_changed_during_copy_is_rejected(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_fsync = os.fsync

    def change_source(fd):
        (repository / "a.py").write_bytes(b"changed during copy")
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", change_source)
    store = tmp_path / "snapshots"
    with pytest.raises(RepositoryError, match="changed during capture"):
        create_snapshot(repository, store)
    assert list(store.iterdir()) == []


def test_hash_describes_bytes_copied_not_earlier_inventory(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_copy = snapshot_module._copy_file

    def change_before_copy(source, destination):
        if source.path == "a.py":
            (repository / "a.py").write_bytes(b"new bytes before copying")
        return original_copy(source, destination)

    monkeypatch.setattr(snapshot_module, "_copy_file", change_before_copy)
    captured = create_snapshot(repository, tmp_path / "snapshots")
    entry = next(entry for entry in captured.entries if entry.path == "a.py")
    assert entry.sha256 == hashlib.sha256(b"new bytes before copying").hexdigest()
    assert (captured.files_root / "a.py").read_bytes() == b"new bytes before copying"


def test_missing_non_directory_and_symlink_roots_are_rejected(tmp_path: Path) -> None:
    regular_file = tmp_path / "file"
    regular_file.write_bytes(b"fixture")
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    for root in (tmp_path / "missing", regular_file, link):
        with pytest.raises(RepositoryError):
            create_snapshot(root, tmp_path / "snapshots")


def test_cannot_exclude_entire_input(repository: Path) -> None:
    for store in (repository, repository.parent):
        with pytest.raises(RepositoryError, match="cannot contain the input root"):
            create_snapshot(repository, store)


def test_empty_repository_and_multi_chunk_file(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    store = tmp_path / "snapshots"
    empty = create_snapshot(root, store)
    assert empty.entries == ()
    assert empty.to_report()["total_bytes"] == 0
    content = bytes(range(256)) * 9000
    (root / "large.bin").write_bytes(content)
    captured = create_snapshot(root, store)
    assert (captured.files_root / "large.bin").read_bytes() == content
    assert captured.entries[0].sha256 == hashlib.sha256(content).hexdigest()
    assert captured.entries[0].size_bytes == len(content)


def test_timestamp_and_storage_location_do_not_affect_identity(
    repository: Path, tmp_path: Path,
) -> None:
    first = create_snapshot(repository, tmp_path / "first")
    os.utime(repository / "a.py", (1234567890, 1234567890))
    second = create_snapshot(repository, tmp_path / "second")
    assert first.snapshot_id == second.snapshot_id


def test_unreadable_directory_aborts_capture(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = os.open

    def denied_open(path, flags, mode=0o777, *, dir_fd=None):
        if path == "package" and dir_fd is not None:
            raise PermissionError("synthetic unreadable directory")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", denied_open)
    store = tmp_path / "snapshots"
    with pytest.raises(RepositoryError, match="unreadable"):
        create_snapshot(repository, store)
    assert list(store.iterdir()) == []


def test_default_storage_is_excluded_without_duplicate_descendants(repository: Path) -> None:
    store = repository / ".repoweave" / "snapshots"
    first = create_snapshot(repository, store)
    second = create_snapshot(repository, store)
    assert first.to_report() == second.to_report()
    assert [exclusion.path for exclusion in first.exclusions] == [".repoweave"]
