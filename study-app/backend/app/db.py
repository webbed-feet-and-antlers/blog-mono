"""Async SQLAlchemy database session + engine setup (SQLite via aiosqlite)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(
    settings.db_url,
    echo=False,
    future=True,
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields an async DB session."""
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create all tables and run lightweight migrations. Called from lifespan."""
    from .models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Guarded migration: add lesson_id to existing documents table.
        # create_all won't mutate an existing table, so we check + ALTER.
        result = await conn.execute(
            text("PRAGMA table_info(documents)")
        )
        columns = {row[1] for row in result.fetchall()}
        if "lesson_id" not in columns:
            await conn.execute(
                text(
                    "ALTER TABLE documents ADD COLUMN lesson_id VARCHAR "
                    "REFERENCES lessons(id)"
                )
            )
            logger.info("Migrated documents table: added lesson_id column")
        # Audio recording support columns.
        if "kind" not in columns:
            await conn.execute(
                text("ALTER TABLE documents ADD COLUMN kind VARCHAR DEFAULT 'text'")
            )
        if "duration_seconds" not in columns:
            await conn.execute(
                text("ALTER TABLE documents ADD COLUMN duration_seconds INTEGER")
            )
        if "transcription_status" not in columns:
            await conn.execute(
                text("ALTER TABLE documents ADD COLUMN transcription_status VARCHAR")
            )
        if "transcription_error" not in columns:
            await conn.execute(
                text("ALTER TABLE documents ADD COLUMN transcription_error TEXT")
            )
