"""Settings for kg-data. Mirrors study-app/backend/app/config.py conventions."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    openrouter_api_key: str | None = None
    openrouter_model: str = "deepseek/deepseek-v4-flash-0731"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    master_seed: int = 42
    concurrency: int = 6

    # Generation scale (CLI flags override).
    worlds: int = 2
    docs_per_world: int = 40


settings = Settings()
