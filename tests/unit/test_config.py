import os
import traceback
from pathlib import Path

import pytest

from repoweave.config import ConfigurationError, Settings, load_settings


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for field in Settings.model_fields.values():
        if field.alias is not None:
            monkeypatch.delenv(field.alias, raising=False)


@pytest.mark.parametrize("contents", [None, ""])
def test_defaults_without_credentials(contents: str | None) -> None:
    if contents is not None:
        Path(".env").write_text(contents)
    settings = load_settings()
    assert settings.data_dir == Path(".repoweave")
    assert settings.execution == "local"
    assert settings.max_candidates == 3
    assert settings.experiment_timeout_seconds == 300
    assert settings.allow_remote_data is False
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    settings.validate_factory()
    settings.validate_tracing()


def test_template_loads_without_credentials() -> None:
    template = Path(__file__).resolve().parents[2] / ".env.example"
    settings = load_settings(template)
    assert settings == Settings()


def test_dotenv_and_environment_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    Path(".env").write_text(
        "REPOWEAVE_MAX_CANDIDATES=2\nREPOWEAVE_PORT=9000\nREPOWEAVE_ALLOW_REMOTE_DATA=true\n"
    )
    monkeypatch.setenv("REPOWEAVE_MAX_CANDIDATES", "5")
    settings = load_settings()
    assert settings.max_candidates == 5
    assert settings.port == 9000
    assert settings.allow_remote_data is True
    assert "REPOWEAVE_PORT" not in os.environ


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_optional_values_are_unset(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    for name in (
        "FACTORY_API_KEY", "REPOWEAVE_FACTORY_MODEL", "LANGSMITH_API_KEY",
        "LANGSMITH_ENDPOINT", "DAYTONA_API_KEY", "REPOWEAVE_DAYTONA_SNAPSHOT",
    ):
        monkeypatch.setenv(name, value)
    settings = load_settings()
    assert settings.factory_api_key is None
    assert settings.factory_model is None
    assert settings.langsmith_api_key is None
    assert settings.langsmith_endpoint is None
    assert settings.daytona_api_key is None
    assert settings.daytona_snapshot is None


@pytest.mark.parametrize(("name", "value"), [
    ("REPOWEAVE_MAX_CANDIDATES", "0"),
    ("REPOWEAVE_MAX_CANDIDATES", "-1"),
    ("REPOWEAVE_MAX_CANDIDATES", "1.5"),
    ("REPOWEAVE_EXPERIMENT_TIMEOUT_SECONDS", "0"),
    ("REPOWEAVE_EXPERIMENT_TIMEOUT_SECONDS", "invalid"),
    ("REPOWEAVE_EXECUTION", "unknown"),
    ("REPOWEAVE_ALLOW_REMOTE_DATA", "maybe"),
    ("LANGSMITH_TRACING", "maybe"),
    ("REPOWEAVE_PORT", "0"),
    ("REPOWEAVE_PORT", "65536"),
])
def test_invalid_settings(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError, match=name):
        load_settings()


def test_errors_do_not_expose_values(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sentinel-credential-never-print"
    monkeypatch.setenv("FACTORY_API_KEY", secret)
    monkeypatch.setenv("REPOWEAVE_MAX_CANDIDATES", secret)
    with pytest.raises(ConfigurationError) as captured:
        load_settings()
    assert "REPOWEAVE_MAX_CANDIDATES" in str(captured.value)
    assert secret not in "".join(traceback.format_exception(captured.value))


def test_secret_fields_are_excluded_from_repr_and_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sentinel-credential-never-persist"
    for name in ("FACTORY_API_KEY", "LANGSMITH_API_KEY", "DAYTONA_API_KEY"):
        monkeypatch.setenv(name, secret)
    settings = load_settings()
    assert settings.factory_api_key is not None
    assert settings.factory_api_key.get_secret_value() == secret
    assert secret not in repr(settings)
    assert secret not in settings.model_dump_json()
    assert not {"factory_api_key", "langsmith_api_key", "daytona_api_key"} & settings.model_dump().keys()


def test_feature_credentials_are_checked_only_when_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPOWEAVE_EXECUTION", "daytona")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    settings = load_settings()
    with pytest.raises(ConfigurationError, match="FACTORY_API_KEY"):
        settings.validate_factory(unattended=True)
    with pytest.raises(ConfigurationError, match="LANGSMITH_API_KEY"):
        settings.validate_tracing()
    with pytest.raises(ConfigurationError, match="DAYTONA_API_KEY"):
        settings.validate_daytona(dataset_id="fixture", approved_dataset_ids=set())


def test_enabled_factory_and_tracing_with_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACTORY_API_KEY", "test-factory-key")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-tracing-key")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    settings = load_settings()
    settings.validate_factory(unattended=True)
    settings.validate_tracing()


def test_daytona_requires_settings_and_dataset_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAYTONA_API_KEY", "test-daytona-key")
    with pytest.raises(ConfigurationError, match="REPOWEAVE_DAYTONA_SNAPSHOT"):
        load_settings().validate_daytona(dataset_id="fixture", approved_dataset_ids={"fixture"})
    monkeypatch.setenv("REPOWEAVE_DAYTONA_SNAPSHOT", "test-snapshot")
    with pytest.raises(ConfigurationError, match="REPOWEAVE_ALLOW_REMOTE_DATA"):
        load_settings().validate_daytona(dataset_id="fixture", approved_dataset_ids={"fixture"})
    monkeypatch.setenv("REPOWEAVE_ALLOW_REMOTE_DATA", "true")
    settings = load_settings()
    for approvals in (set(), {"another-fixture"}):
        with pytest.raises(ConfigurationError, match="explicit remote-data approval"):
            settings.validate_daytona(dataset_id="fixture", approved_dataset_ids=approvals)
    settings.validate_daytona(dataset_id="fixture", approved_dataset_ids={"fixture"})


def test_unreadable_configuration_is_secret_safe() -> None:
    secret = "invalid-secret-bytes"
    Path(".env").write_bytes(secret.encode() + b"\xff")
    with pytest.raises(ConfigurationError) as captured:
        load_settings()
    assert secret not in "".join(traceback.format_exception(captured.value))
