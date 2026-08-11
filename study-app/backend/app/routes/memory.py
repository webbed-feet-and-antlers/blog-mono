"""Debug route — inspect agent memory (POC transparency into the agent's state)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.memory import list_memory
from ..db import get_session
from ..models import ContentItem
from ..schemas import AgentMemoryOut, ContentItemOut

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("", response_model=list[AgentMemoryOut])
async def get_memory(
    scope: str | None = Query(default=None),
    ref_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    return await list_memory(session, scope=scope, ref_id=ref_id)


@router.get("/proactive", response_model=list[ContentItemOut], tags=["proactive"])
async def get_proactive_decks(session: AsyncSession = Depends(get_session)):
    """List all proactive review decks the agent has generated on its own."""
    from sqlalchemy import select

    result = await session.execute(
        select(ContentItem)
        .where(ContentItem.type == "flashcards")
        .order_by(ContentItem.created_at.desc())
    )
    return [
        item
        for item in result.scalars().all()
        if isinstance(item.content, dict)
        and item.content.get("origin") == "proactive"
    ]
