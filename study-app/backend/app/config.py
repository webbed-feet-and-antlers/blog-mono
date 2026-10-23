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
    # Model policy (checked 2026-10-23): keep the newest cheap flash tier,
    # weighted by OUR workload shape — the agent is output-heavy (quiz/
    # explanation generation), and deepseek-v4-flash-0731 (2026-07-31) is
    # still the cheapest on OUTPUT ($0.133/M) of the current flash options,
    # so it stays despite newer arrivals. .env pins OPENROUTER_MODEL anyway;
    # this default is the fallback. Re-check via the live catalog
    # (temporal-kg/kg-data has a `models` command) before bumping.
    openrouter_api_key: str | None = None
    openrouter_model: str = "deepseek/deepseek-v4-flash-0731"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Transcription — uses OpenRouter's /audio/transcriptions endpoint (same key,
    # same base URL as the LLM). qwen3-asr-1.7b is ~$0.000008/sec (~$0.03/hour).
    # NOTE: audio models do NOT appear in the unfiltered /api/v1/models list —
    # query it with ?input_modalities=audio&output_modalities=transcription
    # (verified 2026-10-23; 28 ASR models listed). Cheaper options if volume
    # ever grows: whisper-large-v3-turbo / qwen3-asr-0.6b (~$0.0000033/sec).
    transcription_model: str = "qwen/qwen3-asr-1.7b"
    audio_max_bytes: int = 500 * 1024 * 1024  # 500 MB

    # Evals (backend/evals/) — the judge is a *stronger* model than the
    # generator so a model never grades its own failure modes. Runs at
    # temperature 0 via the same OpenRouter client as everything else.
    # claude-haiku-5.5 (2026-10-07): judge volume is low, so the price step
    # over a flash tier is cents per eval run, and it finally makes the
    # judge genuinely stronger than the generator.
    evals_judge_model: str = "anthropic/claude-haiku-5.5"
    evals_n: int = 10  # cases per suite (EVALS_N; a 25-case deep run takes hours)

    # Paths
    base_dir: Path = Path(__file__).resolve().parent.parent
    storage_dir: Path = base_dir / "storage"
    db_path: Path = base_dir / "study_app.db"
    # The built SPA served by this same process in production (single
    # origin). In dev the dir doesn't exist and the backend is API-only.
    frontend_dist_dir: Path = base_dir.parent / "frontend" / "dist"

    # Postgres (Supabase or any Postgres). Unset → SQLite at db_path, so
    # local dev and tests need zero setup. Supabase's pooler strings
    # (postgres://…:6543/…) are accepted as-is; the asyncpg driver is
    # injected automatically. Use the transaction pooler (port 6543) for
    # the app; alembic/migrations use the same URL via its session mode.
    database_url: str | None = None

    # Auth — Clerk (dashboard.clerk.com). The frontend holds the
    # publishable key (VITE_CLERK_PUBLISHABLE_KEY) and sends the session
    # JWT as a Bearer token (or ?token= for <img>/beacon URLs, which
    # cannot carry headers); the backend verifies it with the secret key.
    clerk_secret_key: str | None = None
    # Origins allowed to hold Clerk sessions (the SDK's azp check). MUST
    # include the production origin when shipping (env: JSON list), or
    # every token 401s.
    clerk_authorized_parties: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # CORS — the browser origins that may call the API (env: JSON list).
    # Keep in sync with clerk_authorized_parties: one says "this origin
    # may call us", the other "Clerk tokens from this origin are valid".
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # Proactive agent — a background job that learns from quiz misses and
    # pre-generates flashcard review decks for weak topics. Default OFF so
    # the app behaves as before until explicitly enabled.
    proactive_enabled: bool = False
    proactive_interval_seconds: int = 1800  # 30 min
    proactive_score_threshold: float = 0.7  # below this counts as "struggled"
    proactive_cooldown_hours: int = 24  # don't regenerate a deck within this

    # Auto-generation: when a document is uploaded and analyzed, automatically
    # generate flashcards in the background. The student never needs to click
    # "Generate" — content is ready when they are.
    auto_generate_flashcards: bool = False

    # Auto-rename: when a document's filename looks machine-generated (hex
    # hashes, IMG_1234, recording-<timestamp>, …), replace it with a clean
    # descriptive title derived from the content. Descriptive names are kept.
    auto_rename_files: bool = True

    # Activity-ledger retention: prune the newest N rows, delete the rest
    # while logging. 5000 keeps SQLite lean; on Postgres set 0 to disable
    # pruning entirely — the ledger is the agent's behavioral memory and
    # storage is cheap there.
    activity_ledger_max_rows: int = 5_000

    # SQLite URL is derived from db_path; Postgres wins when DATABASE_URL
    # is set (driver normalized to asyncpg in app.db).
    @property
    def db_url(self) -> str:
        if self.database_url:
            url = self.database_url
            if url.startswith("postgres://"):
                url = "postgresql+asyncpg://" + url[len("postgres://"):]
            elif url.startswith("postgresql://"):
                url = "postgresql+asyncpg://" + url[len("postgresql://"):]
            return url
        return f"sqlite+aiosqlite:///{self.db_path}"


settings = Settings()
