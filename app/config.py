"""Centralised application settings, loaded from environment variables.

Uses pydantic-settings so we get type coercion, defaults, and a single
source of truth that's easy to override in Docker / CI.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM ---
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    anthropic_model:   str = Field(default="claude-sonnet-4-5-20250929", validation_alias="ANTHROPIC_MODEL")

    # --- Database ---
    database_url:       str = Field(
        default="postgresql+psycopg2://readonly_user:readonly_pw@localhost:5432/shop",
        validation_alias="DATABASE_URL",
    )
    admin_database_url: str = Field(
        default="postgresql+psycopg2://shop_admin:admin_pw@localhost:5432/shop",
        validation_alias="ADMIN_DATABASE_URL",
    )

    # --- Guardrails ---
    max_rows:            int = Field(default=1000,    validation_alias="MAX_ROWS")
    max_subquery_depth:  int = Field(default=3,       validation_alias="MAX_SUBQUERY_DEPTH")
    max_explain_rows:    int = Field(default=1_000_000, validation_alias="MAX_EXPLAIN_ROWS")
    query_timeout_s:     int = Field(default=10,      validation_alias="QUERY_TIMEOUT_S")

    # --- API ---
    api_host:  str = Field(default="0.0.0.0", validation_alias="API_HOST")
    api_port:  int = Field(default=8000,      validation_alias="API_PORT")
    log_level: str = Field(default="INFO",    validation_alias="LOG_LEVEL")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
