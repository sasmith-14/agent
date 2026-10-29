"""Environment settings take precedence so secrets never need to enter YAML."""

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import urlsplit

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

Policy = Literal["allow", "ask", "deny"]


class ConfigError(ValueError):
    """Keep configuration failures separate from runtime provider failures."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GeminiSettings(StrictModel):
    model: str = ""
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"


class OllamaSettings(StrictModel):
    model: str = ""
    host: str = "http://localhost:11434"


class Limits(StrictModel):
    max_steps: int = Field(default=10, gt=0)
    max_total_tokens: int = Field(default=20000, gt=0)


class RetrySettings(StrictModel):
    max_attempts: int = Field(default=3, ge=1)
    base_delay: float = Field(default=0.5, ge=0, allow_inf_nan=False)
    max_delay: float = Field(default=30, ge=0, allow_inf_nan=False)


class PolicySettings(StrictModel):
    default: Policy = "ask"
    tools: dict[str, Policy] = Field(default_factory=dict)


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PILOT_", env_nested_delimiter="__", extra="forbid", populate_by_name=True
    )
    provider: Literal["gemini", "ollama"] = "gemini"
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    gemini_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("GEMINI_API_KEY", "PILOT_GEMINI_API_KEY"),
    )
    limits: Limits = Field(default_factory=Limits)
    retry: RetrySettings = Field(default_factory=RetrySettings)
    timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
    policy: PolicySettings = Field(default_factory=PolicySettings)
    tool_output_max_length: int = Field(default=4000, gt=0)
    runs_dir: Path = Path("runs")

    @model_validator(mode="after")
    def selected_provider_is_configured(self) -> Self:
        selected = self.gemini if self.provider == "gemini" else self.ollama
        if not selected.model.strip():
            raise ValueError(f"{self.provider}.model must be configured")
        for endpoint in (self.gemini.base_url, self.ollama.host):
            try:
                url = urlsplit(endpoint)
                valid = (
                    url.scheme in {"http", "https"}
                    and url.hostname
                    and not (url.username or url.password or url.query or url.fragment)
                )
                _ = url.port
            except ValueError:
                valid = False
            if not valid:
                raise ValueError(
                    "Provider endpoints need an HTTP(S) host without credentials/query"
                )
        return self


def load_config(path: Path | None = None, provider: str | None = None) -> Config:
    """Explicit missing paths are errors; an absent default file permits env-only setup."""
    selected_path = path or Path("config.yaml")
    try:
        data = yaml.safe_load(selected_path.read_text()) if selected_path.exists() else {}
        if path is not None and not path.exists():
            raise ConfigError(f"Config file does not exist: {path}")
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ConfigError("Config YAML must be a mapping")
        if any(not isinstance(key, str) for key in data):
            raise ConfigError("Config keys must be strings")
        if {"gemini_api_key", "GEMINI_API_KEY", "PILOT_GEMINI_API_KEY"} & data.keys():
            raise ConfigError("Set GEMINI_API_KEY in the environment or .env, not YAML")

        class FileConfig(Config):
            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls: type[BaseSettings],
                init_settings: PydanticBaseSettingsSource,
                env_settings: PydanticBaseSettingsSource,
                dotenv_settings: PydanticBaseSettingsSource,
                file_secret_settings: PydanticBaseSettingsSource,
            ) -> tuple[PydanticBaseSettingsSource | Callable[[], dict[str, Any]], ...]:
                # A per-load source avoids shared class state when different files coexist.
                return init_settings, env_settings, dotenv_settings, lambda: data

        return FileConfig(_env_file=".env", **({"provider": provider} if provider else {}))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        # Pydantic errors can echo input values, so never expose their full repr.
        if isinstance(exc, ConfigError):
            raise
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            details = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or 'config'}: {e['msg']}"
                for e in exc.errors(include_input=False)
            )
        else:
            details = "could not read valid YAML/configuration"
        raise ConfigError(f"Invalid configuration: {details}") from None
