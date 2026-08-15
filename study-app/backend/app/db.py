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
    # SQLite busy timeout (seconds): concurrent event-bus handler sessions
    # retry on a locked database instead of raising immediately.
    connect_args={"timeout": 30},
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

    # Enable WAL once, up front: readers don't block writers, which matters
    # now that the event bus runs handler sessions alongside request sessions.
    # Must happen outside any transaction and before pooled connections exist
    # — journal_mode cannot change mid-transaction (a switch attempted inside
    # one leaves the WAL index in a state where writers deadlock until an
    # external connection recovers it).
    import sqlite3

    raw = sqlite3.connect(settings.db_path)
    try:
        raw.execute("PRAGMA journal_mode=WAL")
    finally:
        raw.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Guarded migration: add lesson_id to existing documents table.
        # create_all won't mutate an existing table, so we check + ALTER.
        result = await conn.execute(
            text("PRAGMA table_info(documents)")
        )
        columns = {row[1] for row in result.fetchall()}
        # Module exam date (paces study plans).
        mod_cols = {
            row[1]
            for row in (
                await conn.execute(text("PRAGMA table_info(modules)"))
            ).fetchall()
        }
        if "exam_date" not in mod_cols:
            await conn.execute(
                text("ALTER TABLE modules ADD COLUMN exam_date DATE")
            )
            logger.info("Migrated modules table: added exam_date column")
        if "lesson_id" not in columns:
            await conn.execute(
                text(
                    "ALTER TABLE documents ADD COLUMN lesson_id VARCHAR "
                    "REFERENCES lessons(id)"
                )
            )
            logger.info("Migrated documents table: added lesson_id column")
        # Guarded migration: add module_id so documents can be filed directly
        # under a module (not just a lesson).
        if "module_id" not in columns:
            await conn.execute(
                text(
                    "ALTER TABLE documents ADD COLUMN module_id VARCHAR "
                    "REFERENCES modules(id)"
                )
            )
            logger.info("Migrated documents table: added module_id column")
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
