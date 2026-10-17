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
    assert "unrecognized arguments: pack" in result.stderr
