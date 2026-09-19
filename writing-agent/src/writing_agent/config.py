"""Settings — secrets from env/.env, tunables from config/default.toml."""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent  # writing-agent/
CONFIG_DIR = PACKAGE_ROOT / "config"


class ModelSettings(BaseModel):
    # Drafting/prose. claude-sonnet-5 is the newest Sonnet (and cheaper
    # than 4.6). For max prose quality swap to ~anthropic/claude-opus-latest
    # (~2.5x cost).
    draft: str = "anthropic/claude-sonnet-5"
    # Must return chat-completions logprobs (probed 2026-09: the OpenAI
    # -latest family does; Anthropic models never do).
    scorer: str = "~openai/gpt-mini-latest"


class SamplingSettings(BaseModel):
    architect_temp: float = 0.2
    stylist_temp: float = 0.9
    stylist_top_p: float = 0.92
    editor_temp: float = 0.15


class ThresholdSettings(BaseModel):
    sentence_sd_min: float = 8.0
    sentence_variance_min: float = 15.0
    transition_density_max: float = 0.15
    em_dash_per_1k_max: float = 6.0
    banned_hits_max: int = 0
    deep_syntax_min_per_block: int = 1
    # Continuation-predictability gate: primary signal is the blind
    # scorer's mean token surprisal (bits) continuing the block's context —
    # confident continuations (<= bits_max) read as normalized/slop.
    # Token overlap is only the fallback when logprobs are unavailable.
    continuation_overlap_max: float = 0.55
    continuation_bits_max: float = 0.9
    max_revisions: int = 3
    # Discourse-shape gate (StoryScope, arXiv:2604.03136 — see
    # scoring/discourse.py). Docs under 400 words skip the timeline and
    # resolution gates.
    temporal_markers_min_per_1k: float = 0.5
    unresolved_min: int = 1
    top5_share_max: float = 0.18
    judge_flag_at: float = 0.7


class Settings(BaseSettings):
    # Absolute path: .env must load no matter which directory the server or
    # CLI is launched from (cwd-relative env_file silently misses the key).
    model_config = SettingsConfigDict(
        env_file=PACKAGE_ROOT / ".env", extra="ignore"
    )

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    workspace_dir: Path = PACKAGE_ROOT / "workspace"

    models: ModelSettings = ModelSettings()
    sampling: SamplingSettings = SamplingSettings()
    thresholds: ThresholdSettings = ThresholdSettings()


@lru_cache
def get_settings() -> Settings:
    """Env (secrets) layered over config/default.toml (tunables)."""
    data: dict = {}
    toml_path = CONFIG_DIR / "default.toml"
    if toml_path.exists():
        data = tomllib.loads(toml_path.read_text())
    return Settings(
        models=ModelSettings(**data.get("models", {})),
        sampling=SamplingSettings(**data.get("sampling", {})),
        thresholds=ThresholdSettings(**data.get("thresholds", {})),
    )
