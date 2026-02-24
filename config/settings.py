"""Application settings loaded from environment variables.

Uses pydantic-settings for validation and type coercion. App-specific
settings use the ``HAQSETU_`` prefix; GCP / infrastructure settings use
their canonical environment variable names via ``validation_alias``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Central configuration for the HaqSetu application.

    Environment variables are loaded from a ``.env`` file when present.
    App-specific keys are prefixed with ``HAQSETU_``; GCP / infra keys
    use their standard names (configured via ``validation_alias``).
    """

    model_config = SettingsConfigDict(
        env_prefix="HAQSETU_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── App ────────────────────────────────────────────────────────────
    env: Literal["development", "production"] = "development"

    # ── GCP ────────────────────────────────────────────────────────────
    gcp_project_id: str = Field(default="", validation_alias="GCP_PROJECT_ID")
    gcp_region: str = Field(default="asia-south1", validation_alias="GCP_REGION")
    google_application_credentials: str = Field(default="", validation_alias="GOOGLE_APPLICATION_CREDENTIALS")

    # ── Vertex AI / Gemini ─────────────────────────────────────────────
    vertex_ai_model: str = Field(default="gemini-2.0-flash", validation_alias="VERTEX_AI_MODEL")
    vertex_ai_location: str = Field(default="asia-south1", validation_alias="VERTEX_AI_LOCATION")

    # ── Redis ──────────────────────────────────────────────────────────
    redis_url: str = Field(default="redis://localhost:6379/0", validation_alias="REDIS_URL")

    # ── Firestore ──────────────────────────────────────────────────────
    firestore_database: str = Field(default="(default)", validation_alias="FIRESTORE_DATABASE")

    # ── API ────────────────────────────────────────────────────────────
    api_host: str = Field(default="0.0.0.0", validation_alias="API_HOST")
    api_port: int = Field(default=8000, validation_alias="API_PORT")
    api_workers: int = Field(default=4, validation_alias="API_WORKERS")

    # ── Rate Limiting ──────────────────────────────────────────────────
    rate_limit_per_minute: int = Field(default=60, validation_alias="RATE_LIMIT_PER_MINUTE")

    # ── Logging ────────────────────────────────────────────────────────
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_format: str = Field(default="json", validation_alias="LOG_FORMAT")

    # ── DPDPA Compliance ───────────────────────────────────────────────
    dpdpa_consent_retention_years: int = Field(default=7, validation_alias="DPDPA_CONSENT_RETENTION_YEARS")
    encryption_key: str = Field(default="", validation_alias="ENCRYPTION_KEY")

    # ── Cache TTLs (seconds) ───────────────────────────────────────────
    translation_cache_ttl: int = Field(default=2_592_000, validation_alias="TRANSLATION_CACHE_TTL")  # 30 days
    scheme_cache_ttl: int = Field(default=14_400, validation_alias="SCHEME_CACHE_TTL")  # 4 hours
    session_cache_ttl: int = Field(default=3_600, validation_alias="SESSION_CACHE_TTL")  # 1 hour

    # ── Derived Properties ─────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        return self.env == Environment.PRODUCTION


# Module-level singleton — import ``settings`` everywhere.
settings = Settings()
