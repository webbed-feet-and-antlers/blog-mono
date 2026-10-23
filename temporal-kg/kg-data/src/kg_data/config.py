"""Settings for kg-data. Mirrors study-app/backend/app/config.py conventions."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    openrouter_api_key: str | None = None
    # Model policy (checked 2026-10-23): newest cheap flash tier beats older
    # "mini" models on cost AND quality — glm-5.3-flash is $0.005/M prompt,
    # 1M ctx, structured-JSON verified. Re-check with `python -m kg_data
    # models` and bump this default when something newer/cheaper lands.
    openrouter_model: str = "z-ai/glm-5.3-flash"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    master_seed: int = 42
    # 6+ caused empty-response storms on every provider tried (2026-10-23);
    # 3-4 is the sweet spot for small structured calls.
    concurrency: int = 3

    # Generation scale (CLI flags override).
    worlds: int = 2
    docs_per_world: int = 40


settings = Settings()
