"""Async SQLAlchemy database session + engine setup (SQLite via aiosqlite)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import settings

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
    """Create all tables. Called from the FastAPI lifespan."""
    from .models import Base  # noqa: F811 — imported here to avoid circular import

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
