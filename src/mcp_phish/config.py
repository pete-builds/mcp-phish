"""Validated, env-driven configuration for mcp-phish.

Loads values from environment variables (and a ``.env`` file when present),
validates types/ranges, and refuses to start in real mode without the bits it
needs to talk to api.phish.net. Stub mode requires no upstream credentials.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the MCP Phish server.

    All fields can be overridden via environment variables. Names map 1:1 with
    the env var names (case-insensitive). Pydantic validates them at startup.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Mode toggle
    # ------------------------------------------------------------------
    stub_mode: bool = Field(
        default=True,
        description=(
            "If True, every tool returns realistic mock data with no network "
            "calls. If False, the server hits api.phish.net v5 and phish.in v2."
        ),
    )

    # ------------------------------------------------------------------
    # Upstream API credentials (required when stub_mode=False)
    # ------------------------------------------------------------------
    phishnet_api_key: str = Field(
        default="",
        description="Free API key from https://api.phish.net/keys/.",
    )
    phishin_api_key: str = Field(
        default="",
        description="Optional. phish.in works anonymously; a key raises caps.",
    )
    phishnet_base_url: str = Field(default="https://api.phish.net/v5")
    phishin_base_url: str = Field(default="https://phish.in/api/v2")

    # ------------------------------------------------------------------
    # Cache (aiosqlite opaque KV)
    # ------------------------------------------------------------------
    cache_db_path: str = Field(default="/data/phish-cache.db")
    cache_ttl_seconds: int = Field(default=86400, ge=60, le=7 * 86400)

    # ------------------------------------------------------------------
    # Per-instance throttle (requests/second)
    # ------------------------------------------------------------------
    throttle_phishnet_rps: float = Field(default=5.0, gt=0, le=50)
    throttle_phishin_rps: float = Field(default=10.0, gt=0, le=50)

    # ------------------------------------------------------------------
    # MCP server settings
    # ------------------------------------------------------------------
    mcp_host: str = Field(default="0.0.0.0")
    mcp_port: int = Field(default=3705, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(default="INFO")
    log_format: Literal["json", "text"] = Field(
        default="json",
        description="Structured JSON logs (production) or human-readable text.",
    )

    # ------------------------------------------------------------------
    # Vault (Phase 3 read path)
    # ------------------------------------------------------------------
    vault_enabled: bool = Field(
        default=False,
        description=(
            "If True, read tools use the phish-vault Postgres database instead of "
            "the live API. Hot-window shows always read live regardless of this flag."
        ),
    )
    vault_hot_window_hours: int = Field(
        default=24,
        ge=1,
        description="Shows newer than this many hours are always read from the live API.",
    )
    vault_max_stale_hours: int = Field(
        default=36,
        ge=1,
        description=(
            "Refuse to serve vault data if the last successful ETL run is older "
            "than this many hours. Health reports 'degraded'."
        ),
    )
    pg_host: str = Field(default="postgres")
    pg_port: int = Field(default=5432, ge=1, le=65535)
    pg_db: str = Field(default="phish")
    pg_user: str = Field(default="phish")
    pg_password: SecretStr = Field(default=SecretStr(""))

    @property
    def pg_dsn(self) -> str:
        """Build a PostgreSQL DSN from vault connection settings."""
        pw = self.pg_password.get_secret_value()
        return f"postgresql://{self.pg_user}:{pw}@{self.pg_host}:{self.pg_port}/{self.pg_db}"

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _check_real_mode_requirements(self) -> Settings:
        if not self.stub_mode and not self.phishnet_api_key:
            raise ValueError(
                "Real mode requires PHISHNET_API_KEY. Register a free key at "
                "https://api.phish.net/keys/ or set STUB_MODE=true."
            )
        return self

    def safe_repr(self) -> dict[str, object]:
        """Return a redacted dict suitable for logging at startup."""
        return {
            "stub_mode": self.stub_mode,
            "phishnet_api_key_set": bool(self.phishnet_api_key),
            "phishin_api_key_set": bool(self.phishin_api_key),
            "phishnet_base_url": self.phishnet_base_url,
            "phishin_base_url": self.phishin_base_url,
            "cache_db_path": self.cache_db_path,
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "throttle_phishnet_rps": self.throttle_phishnet_rps,
            "throttle_phishin_rps": self.throttle_phishin_rps,
            "mcp_host": self.mcp_host,
            "mcp_port": self.mcp_port,
            "log_level": self.log_level,
            "log_format": self.log_format,
            "vault_enabled": self.vault_enabled,
            "pg_host": self.pg_host,
            "pg_port": self.pg_port,
            "pg_db": self.pg_db,
            # pg_password intentionally omitted
        }


def load_settings() -> Settings:
    """Build a Settings instance from the environment. Raises on invalid config."""
    return Settings()
