"""Application configuration loaded from environment via pydantic-settings."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # LLM via OpenRouter (one key works across all providers/models).
    # OpenRouter exposes an OpenAI-compatible API at https://openrouter.ai/api/v1,
    # so we use the openai SDK pointed at that base URL. Swap models just by
    # changing OPENROUTER_MODEL (e.g. "anthropic/claude-sonnet-4",
    # "openai/gpt-4o", "google/gemini-flash-1.5").
    openrouter_api_key: str | None = None
    openrouter_model: str = "deepseek/deepseek-v4-flash"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Paths
    base_dir: Path = Path(__file__).resolve().parent.parent
    storage_dir: Path = base_dir / "storage"
    db_path: Path = base_dir / "study_app.db"

    # Proactive agent — a background job that learns from quiz misses and
    # pre-generates flashcard review decks for weak topics. Default OFF so
    # the app behaves as before until explicitly enabled.
    proactive_enabled: bool = False
    proactive_interval_seconds: int = 1800  # 30 min
    proactive_score_threshold: float = 0.7  # below this counts as "struggled"
    proactive_cooldown_hours: int = 24  # don't regenerate a deck within this

    # SQLite URL is derived from db_path.
    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"


settings = Settings()
