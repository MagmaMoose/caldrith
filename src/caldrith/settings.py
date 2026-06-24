"""Application configuration sourced from environment variables.

In production these are injected as Kubernetes env from an OCI Vault
ExternalSecret; locally they may come from a ``.env`` file. The PEM private key is
read as a single string (env vars cannot hold real newlines portably), so we accept
either literal newlines or the common ``\\n``-escaped form and normalize it.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    """Caldrith runtime configuration.

    Attributes mirror the secrets contract documented in the project brief. All
    values are read from the process environment (case-insensitive) or a ``.env``
    file if present.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_id: str = Field(..., description="GitHub App ID.")
    private_key: str = Field(..., description="GitHub App private key (PEM string).")
    webhook_secret: str = Field(..., description="Shared secret for webhook HMAC verification.")
    redis_url: str = Field("redis://localhost:6379", description="Redis connection URL for ARQ.")

    # Configurable for the GHES future — never hardcode api.github.com downstream.
    github_api_url: str = Field("https://api.github.com", description="GitHub REST API base URL.")

    admin_repo: str = Field("admin", description="Name of the admin (config) repository.")
    config_path: str = Field(".github", description="Directory holding the settings file.")
    settings_file_path: str = Field("settings.yml", description="Settings file name.")

    # Manual + scheduled reconcile (break-glass when webhooks are silent).
    manual_trigger_token: str | None = Field(
        default=None,
        description=(
            "Bearer token guarding POST /reconcile. Unset disables the endpoint — set "
            "to a long random string to enable manual reconciles."
        ),
    )
    reconcile_cron_minutes: int = Field(
        default=0,
        ge=0,
        le=1440,
        description=(
            "Periodic full reconcile across every installation, every N minutes. 0 (the "
            "default) disables the cron. Use e.g. 60 to belt-and-brace against missed "
            "webhooks; values that don't evenly divide 60 only run at matching minutes."
        ),
    )

    @field_validator("private_key")
    @classmethod
    def _normalize_private_key(cls, value: str) -> str:
        """Accept ``\\n``-escaped PEM strings (common in env/secret stores)."""
        if "\\n" in value and "-----BEGIN" in value:
            return value.replace("\\n", "\n")
        return value


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Return a process-wide cached :class:`AppConfig` instance."""
    return AppConfig()  # type: ignore[call-arg]  # values come from the environment
