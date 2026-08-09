"""Debug route — inspect agent memory (POC transparency into the agent's state)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.memory import list_memory
from ..db import get_session
from ..schemas import AgentMemoryOut

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("", response_model=list[AgentMemoryOut])
async def get_memory(
    scope: str | None = Query(default=None),
    ref_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    return await list_memory(session, scope=scope, ref_id=ref_id)
