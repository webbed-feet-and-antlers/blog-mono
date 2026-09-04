"""Async SQLAlchemy database session + engine setup.

SQLite (aiosqlite) by default; Postgres (asyncpg) when DATABASE_URL is set
(Supabase in production). Schema management is alembic (migrations/) —
init_db() upgrades to head at startup, stamping legacy pre-alembic SQLite
files to the baseline revision instead of replaying it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import settings

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent


def _engine_kwargs() -> dict:
    if settings.db_url.startswith("sqlite"):
        # SQLite busy timeout (seconds): concurrent event-bus handler
        # sessions retry on a locked database instead of raising immediately.
        return {"connect_args": {"timeout": 30}}
    # asyncpg through Supabase's transaction pooler: the pooler doesn't
    # support session-level prepared statements, and asyncpg's automatic
    # statement cache then fails subtly (protocol errors, rare wrong
    # results) — disable it. pool_pre_ping survives the pooler recycling
    # server connections under us.
    return {"pool_pre_ping": True, "connect_args": {"statement_cache_size": 0}}


engine = create_async_engine(
    settings.db_url,
    echo=False,
    future=True,
    **_engine_kwargs(),
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


def _alembic_config():
    from alembic.config import Config

    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "migrations"))
    return cfg


async def init_db() -> None:
    """Bring the schema to head via alembic. Called from the app lifespan.

    SQLite additionally gets WAL enabled first: readers don't block
    writers, which matters now that the event bus runs handler sessions
    alongside request sessions. The switch must happen outside any
    transaction and before pooled connections exist — journal_mode cannot
    change mid-transaction (a switch attempted inside one leaves the WAL
    index in a state where writers deadlock until an external connection
    recovers it).
    """
    from alembic import command

    if engine.dialect.name == "sqlite":
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        import sqlite3

        raw = sqlite3.connect(settings.db_path)
        try:
            raw.execute("PRAGMA journal_mode=WAL")
        finally:
            raw.close()

        # A pre-alembic dev DB already carries the baseline schema (the
        # old guarded-ALTER path applied it); stamp it so `upgrade head`
        # only applies revisions after the baseline instead of failing on
        # CREATE TABLE duplicates. "Pre-alembic" = no version rows yet —
        # alembic_version can exist but be empty (e.g. an autogenerate
        # run against this DB created the table without stamping).
        def _probe(sync_conn) -> tuple[str | None, set[str]]:
            from alembic.migration import MigrationContext

            current = MigrationContext.configure(sync_conn).get_current_revision()
            return current, set(inspect(sync_conn).get_table_names())

        async with engine.connect() as conn:
            current_rev, tables = await conn.run_sync(_probe)
        if current_rev is None and "modules" in tables:
            await asyncio.to_thread(command.stamp, _alembic_config(), "baseline")
            logger.info("Stamped legacy pre-alembic database at 'baseline'")

    await asyncio.to_thread(command.upgrade, _alembic_config(), "head")
