import subprocess
import sys
from pathlib import Path

import pytest


def cli(tmp_path: Path, *arguments: str, environment: dict[str, str] | None = None):
    return subprocess.run(
        [str(Path(sys.executable).with_name("repoweave")), *arguments],
        cwd=tmp_path, env={} if environment is None else environment,
        capture_output=True, text=True, check=False,
    )


@pytest.mark.parametrize("selection", ["stored", "zstd", "lzma", "policy"])
def test_cli_round_trip_without_credentials(tmp_path: Path, selection: str) -> None:
    source = tmp_path / "source"
    source.mkdir()
    data = b"owned fixture\r\n\x00\xff" * 1000
    (source / "script").write_bytes(data)
    (source / "script").chmod(0o751)
    (source / ".env").write_bytes(b"SYNTHETIC_SECRET=never-print")
    if selection == "policy":
        policy = Path(__file__).resolve().parents[2] / "policies" / "baseline.json"
        options = ("--policy", str(policy))
    else:
        options = ("--codec", selection)
    packed = cli(tmp_path, "pack", "source", "-o", ".repoweave/demo.rpw", *options)
    assert packed.returncode == 0, packed.stderr
    assert "secret-name" in packed.stdout
    assert "never-print" not in packed.stdout + packed.stderr
    # Decoder operation does not depend on even valid application configuration.
    (tmp_path / ".env").write_bytes(b"REPOWEAVE_MAX_CANDIDATES=invalid\n")
    restored = cli(tmp_path, "unpack", ".repoweave/demo.rpw", "-o", ".repoweave/restored",
                   environment={"REPOWEAVE_EXECUTION": "invalid"})
    assert restored.returncode == 0, restored.stderr
    (tmp_path / ".env").unlink()
    assert (tmp_path / ".repoweave/restored/script").read_bytes() == data
    assert (tmp_path / ".repoweave/restored/script").stat().st_mode & 0o111 == 0o111
    compared = cli(tmp_path, "verify", "source", ".repoweave/restored")
    assert compared.returncode == 0, compared.stderr
    assert "Verified:" in compared.stdout
    (tmp_path / ".repoweave/restored/extra").write_bytes(b"extra")
    mismatch = cli(tmp_path, "verify", "source", ".repoweave/restored")
    assert mismatch.returncode != 0
    assert "differ" in mismatch.stderr and "Traceback" not in mismatch.stderr


def test_cli_custom_exclusions_and_existing_output(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_bytes(b"fixture")
    packed = cli(tmp_path, "pack", "source", "-o", "source/reports/demo.rpw")
    assert packed.returncode == 0, packed.stderr
    unpacked = cli(tmp_path, "unpack", "source/reports/demo.rpw", "-o", "restored")
    assert unpacked.returncode == 0, unpacked.stderr
    assert cli(tmp_path, "verify", "source", "restored").returncode != 0
    assert cli(tmp_path, "verify", "source", "restored", "--exclude", "reports").returncode == 0
    before = (source / "reports/demo.rpw").read_bytes()
    assert cli(tmp_path, "pack", "source", "-o", "source/reports/demo.rpw").returncode != 0
    assert (source / "reports/demo.rpw").read_bytes() == before
    assert cli(tmp_path, "unpack", "source/reports/demo.rpw", "-o", "restored").returncode != 0


def test_cli_reports_corruption_and_missing_arguments(tmp_path: Path) -> None:
    (tmp_path / "bad.rpw").write_bytes(b"not an archive")
    result = cli(tmp_path, "unpack", "bad.rpw", "-o", "restored")
    assert result.returncode != 0 and "Traceback" not in result.stderr
    assert not (tmp_path / "restored").exists()
    for command in ("pack", "unpack", "verify"):
        assert cli(tmp_path, command).returncode == 2
