"""Agent memory read/write helpers backed by the agent_memory table.

Scope:
  - "doc":  ref_id = document_id. Caches analysis, extracted concepts, prior generations.
  - "user": ref_id = "" (single local user for the POC). Cross-document learnings.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AgentMemory


async def read_memory(
    session: AsyncSession, scope: str, ref_id: str, key: str
) -> Any | None:
    """Return a single memory value, or None if absent."""
    result = await session.execute(
        select(AgentMemory.value).where(
            AgentMemory.scope == scope,
            AgentMemory.ref_id == ref_id,
            AgentMemory.key == key,
        )
    )
    row = result.first()
    return row[0] if row is not None else None


async def read_memory_scope(
    session: AsyncSession, scope: str, ref_id: str
) -> dict[str, Any]:
    """Return all key/value entries for a given scope+ref as a dict."""
    result = await session.execute(
        select(AgentMemory.key, AgentMemory.value).where(
            AgentMemory.scope == scope,
            AgentMemory.ref_id == ref_id,
        )
    )
    return {k: v for k, v in result.all()}


async def write_memory(
    session: AsyncSession, scope: str, ref_id: str, key: str, value: Any
) -> None:
    """Upsert a memory entry (SQLite ON CONFLICT update)."""
    stmt = sqlite_insert(AgentMemory).values(
        id=uuid.uuid4().hex[:12],
        scope=scope,
        ref_id=ref_id,
        key=key,
        value=value,
    )
    # On conflict (same scope+ref_id+key), update value + updated_at.
    update_cols = {
        "value": stmt.excluded.value,
    }
    from datetime import datetime, timezone

    update_cols["updated_at"] = datetime.now(timezone.utc)
    stmt = stmt.on_conflict_do_update(
        index_elements=[AgentMemory.scope, AgentMemory.ref_id, AgentMemory.key],
        set_=update_cols,
    )
    await session.execute(stmt)


async def list_memory(
    session: AsyncSession, scope: str | None = None, ref_id: str | None = None
) -> list[AgentMemory]:
    """List memory rows (optionally filtered) — used by the debug endpoint."""
    stmt = select(AgentMemory)
    if scope is not None:
        stmt = stmt.where(AgentMemory.scope == scope)
    if ref_id is not None:
        stmt = stmt.where(AgentMemory.ref_id == ref_id)
    stmt = stmt.order_by(AgentMemory.updated_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


def _as_jsonable(value: Any) -> Any:
    """Best-effort coercion to JSON-native types for storage."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return {"raw": str(value)}
