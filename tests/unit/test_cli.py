import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("arguments", [["--help"], []])
def test_installed_cli_without_credentials(tmp_path: Path, arguments: list[str]) -> None:
    result = subprocess.run(
        [str(Path(sys.executable).with_name("repoweave")), *arguments],
        cwd=tmp_path, env={}, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert result.stderr == ""
    if arguments:
        assert "usage: repoweave" in result.stdout
        assert "Repository-aware lossless compression experiments" in result.stdout


def test_cli_configuration_error_is_secret_safe(tmp_path: Path) -> None:
    secret = "sentinel-secret-not-for-output"
    result = subprocess.run(
        [sys.executable, "-m", "repoweave.cli"],
        cwd=tmp_path,
        env={"REPOWEAVE_MAX_CANDIDATES": secret, "FACTORY_API_KEY": secret},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert "REPOWEAVE_MAX_CANDIDATES" in result.stderr
    assert secret not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_unimplemented_command_is_rejected(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "repoweave.cli", "pack"],
        cwd=tmp_path, env={}, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert "invalid choice: 'pack'" in result.stderr


def test_analyze_captures_input_without_credentials(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "hello.txt").write_bytes(b"hello\r\n\x00\xff")
    (root / ".env").write_text("SYNTHETIC_SECRET=must-not-appear")
    report = root / "reports" / "analysis.json"
    data_dir = root / "capture-data"
    result = subprocess.run(
        [sys.executable, "-m", "repoweave.cli", "analyze", str(root), "--output", str(report)],
        cwd=tmp_path, env={"REPOWEAVE_DATA_DIR": str(data_dir)},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    contents = json.loads(report.read_text())
    assert contents["scope"] == "regular-files-directories-executable-v1"
    assert contents["total_bytes"] == 9
    assert [entry["path"] for entry in contents["entries"]] == ["hello.txt"]
    assert {entry["path"] for entry in contents["exclusions"]} == {
        ".env", "capture-data", "reports",
    }
    assert "must-not-appear" not in result.stdout + result.stderr + report.read_text()
    captures = list((data_dir / "snapshots").iterdir())
    assert len(captures) == 1
    assert (captures[0] / "files" / "hello.txt").read_bytes() == b"hello\r\n\x00\xff"


def test_analyze_report_at_input_root_and_custom_data_exclusion(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "file").write_bytes(b"fixture")
    report = root / "analysis.json"
    result = subprocess.run(
        [sys.executable, "-m", "repoweave.cli", "analyze", str(root), "--output", str(report)],
        cwd=tmp_path, env={"REPOWEAVE_DATA_DIR": str(tmp_path / "data")},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    contents = json.loads(report.read_text())
    assert [entry["path"] for entry in contents["entries"]] == ["file"]
    assert contents["exclusions"] == [{"path": "analysis.json", "reason": "output/data"}]


@pytest.mark.parametrize("existing", [False, True])
def test_analyze_reports_failures_without_destroying_output(tmp_path: Path, existing: bool) -> None:
    root = tmp_path / "source"
    root.mkdir()
    report = tmp_path / "analysis.json"
    if existing:
        report.write_bytes(b"existing report")
    else:
        root = tmp_path / "missing"
    result = subprocess.run(
        [sys.executable, "-m", "repoweave.cli", "analyze", str(root), "--output", str(report)],
        cwd=tmp_path, env={}, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    if existing:
        assert "already exists" in result.stderr
        assert report.read_bytes() == b"existing report"
    else:
        assert "Input must be an existing directory" in result.stderr
        assert not report.exists()


def test_report_inside_missing_input_does_not_create_input(tmp_path: Path) -> None:
    root = tmp_path / "missing"
    result = subprocess.run(
        [sys.executable, "-m", "repoweave.cli", "analyze", str(root),
         "--output", str(root / "report.json")],
        cwd=tmp_path, env={}, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert not root.exists()


def test_report_created_during_capture_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from repoweave import cli

    root = tmp_path / "source"
    root.mkdir()
    report = tmp_path / "report.json"
    original_capture = cli.create_snapshot

    def capture_and_create_report(*args, **kwargs):
        snapshot = original_capture(*args, **kwargs)
        report.write_bytes(b"another writer's output")
        return snapshot

    monkeypatch.setattr(cli, "create_snapshot", capture_and_create_report)
    with pytest.raises(FileExistsError):
        cli._analyze(root, report, tmp_path / "data")
    assert report.read_bytes() == b"another writer's output"
    assert len(list((tmp_path / "data" / "snapshots").iterdir())) == 1
