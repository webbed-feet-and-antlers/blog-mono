"""One-shot: copy a study-app SQLite database into a Postgres DATABASE_URL.

Usage (from study-app/backend/):
    DATABASE_URL="postgresql://…TARGET…" DB_PATH=./study_app.db uv run python scripts/sqlite_to_pg.py

Reads every table from the SQLite file in foreign-key order and inserts
the rows into the target as Core inserts (column values round-trip
through the ORM type processors, so JSON/datetime stay portable). Run it
against an EMPTY Postgres database whose schema is already at head
(`uv run alembic upgrade head` against the same DATABASE_URL).
document_chunks is skipped — it's Postgres-only and starts empty.

Safe to re-run only on an empty target; there is no upsert/merge logic.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base


async def main() -> None:
    sqlite_url = f"sqlite+aiosqlite:///{settings.db_path}"
    target_url = settings.db_url
    if target_url.startswith("sqlite"):
        raise SystemExit("DATABASE_URL must point at the Postgres target.")

    src = create_async_engine(sqlite_url)
    dst = create_async_engine(target_url, pool_pre_ping=True)
    src_session = async_sessionmaker(src, expire_on_commit=False)()
    dst_session = async_sessionmaker(dst, expire_on_commit=False)()

    total = 0
    try:
        for table in Base.metadata.sorted_tables:
            if table.name == "document_chunks":
                continue
            result = await src_session.execute(select(table))
            payload = [dict(row._mapping) for row in result]
            if not payload:
                continue
            await dst_session.execute(table.insert(), payload)
            print(f"{table.name}: {len(payload)} rows")
            total += len(payload)
        await dst_session.commit()
        print(f"Done — {total} rows copied to {target_url.split('@')[-1]}")
    finally:
        await src_session.close()
        await dst_session.close()
        await src.dispose()
        await dst.dispose()


if __name__ == "__main__":
    asyncio.run(main())
