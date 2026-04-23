"""Application configuration loaded from environment + .env files."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Top-level settings. Loaded from env vars + .env file."""

    model_config = SettingsConfigDict(
        env_prefix="MM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Wiki storage location (filesystem source of truth)
    wiki_root: Path = Field(
        default=Path("./wiki"),
        description="Root directory for the firm-level knowledge wiki (markdown files).",
    )

    # Observability log location (component 0.4)
    observability_root: Path = Field(
        default=Path("./.observability"),
        description="Root directory for append-only audit logs.",
    )

    # Database (component 0.6 checkpoints, memory retrieval index)
    database_url: str = Field(
        default="",
        description="Postgres connection string. Empty = use PGLite embedded.",
    )

    # LLM provider selection (runtime)
    llm_provider: str = Field(
        default="anthropic",
        description="Default LLM provider: anthropic | openai | gemini.",
    )
    llm_model: str = Field(
        default="claude-sonnet-4-6",
        description="Default model identifier.",
    )


def get_settings() -> Settings:
    """Factory for Settings. Separate function so tests can override."""
    return Settings()
