"""Load settings at application entrypoints; check integrations only when used."""

import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator


class ConfigurationError(ValueError):
    """A configuration failure safe to display without exposing input values."""


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    data_dir: Path = Field(default=Path(".repoweave"), alias="REPOWEAVE_DATA_DIR")
    execution: Literal["local", "daytona"] = Field(default="local", alias="REPOWEAVE_EXECUTION")
    max_candidates: int = Field(default=3, gt=0, alias="REPOWEAVE_MAX_CANDIDATES")
    experiment_timeout_seconds: int = Field(
        default=300, gt=0, alias="REPOWEAVE_EXPERIMENT_TIMEOUT_SECONDS"
    )
    allow_remote_data: bool = Field(default=False, alias="REPOWEAVE_ALLOW_REMOTE_DATA")
    factory_api_key: SecretStr | None = Field(
        default=None, alias="FACTORY_API_KEY", exclude=True, repr=False
    )
    factory_model: str | None = Field(default=None, alias="REPOWEAVE_FACTORY_MODEL")
    langsmith_tracing: bool = Field(default=False, alias="LANGSMITH_TRACING")
    langsmith_api_key: SecretStr | None = Field(
        default=None, alias="LANGSMITH_API_KEY", exclude=True, repr=False
    )
    langsmith_project: str = Field(default="repoweave-dev", alias="LANGSMITH_PROJECT", min_length=1)
    langsmith_endpoint: str | None = Field(default=None, alias="LANGSMITH_ENDPOINT")
    daytona_api_key: SecretStr | None = Field(
        default=None, alias="DAYTONA_API_KEY", exclude=True, repr=False
    )
    daytona_snapshot: str | None = Field(default=None, alias="REPOWEAVE_DAYTONA_SNAPSHOT")
    host: str = Field(default="127.0.0.1", alias="REPOWEAVE_HOST", min_length=1)
    port: int = Field(default=8000, ge=1, le=65535, alias="REPOWEAVE_PORT")

    @field_validator(
        "factory_api_key", "factory_model", "langsmith_api_key", "langsmith_endpoint",
        "daytona_api_key", "daytona_snapshot", mode="before",
    )
    @classmethod
    def blank_as_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def validate_factory(self, *, unattended: bool = False) -> None:
        if unattended and self.factory_api_key is None:
            raise ConfigurationError("FACTORY_API_KEY is required for unattended Factory execution")

    def validate_tracing(self) -> None:
        if self.langsmith_tracing and self.langsmith_api_key is None:
            raise ConfigurationError("LANGSMITH_API_KEY is required when LANGSMITH_TRACING is enabled")

    def validate_daytona(
        self, *, dataset_id: str,
        approved_dataset_ids: set[str] | frozenset[str] | list[str] | tuple[str, ...],
    ) -> None:
        """Call before staging data, using the approvals persisted with the run."""
        if self.daytona_api_key is None:
            raise ConfigurationError("DAYTONA_API_KEY is required for Daytona jobs")
        if self.daytona_snapshot is None:
            raise ConfigurationError("REPOWEAVE_DAYTONA_SNAPSHOT is required for Daytona jobs")
        if not self.allow_remote_data:
            raise ConfigurationError("REPOWEAVE_ALLOW_REMOTE_DATA must be enabled for Daytona jobs")
        if (
            not isinstance(approved_dataset_ids, (set, frozenset, list, tuple))
            or not all(isinstance(value, str) for value in approved_dataset_ids)
        ):
            raise ConfigurationError("Remote-data approvals must be a collection of dataset IDs")
        if not dataset_id or dataset_id not in approved_dataset_ids:
            raise ConfigurationError("The selected dataset requires explicit remote-data approval")


def load_settings(env_file: Path = Path(".env")) -> Settings:
    """Read the selected .env without mutating the process; environment values win."""
    try:
        values = {**dotenv_values(env_file, interpolate=False), **os.environ}
    except (OSError, UnicodeError):
        raise ConfigurationError("Unable to read the configuration file") from None
    inputs = {
        field.alias: values[field.alias]
        for field in Settings.model_fields.values()
        if field.alias is not None and field.alias in values
    }
    try:
        return Settings.model_validate(inputs)
    except ValidationError as error:
        # Only field names cross the CLI boundary, even if a secret was pasted into another field.
        names = sorted({str(item["loc"][0]) for item in error.errors(include_input=False)})
        raise ConfigurationError(f"Invalid configuration: {', '.join(names)}") from None
