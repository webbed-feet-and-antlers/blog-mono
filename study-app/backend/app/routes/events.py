"""Event log route — read-only view of the domain event bus's audit trail.

Every published event and every handler run (ok or failed) lands in the
agent_events table. This endpoint is the "what did the agent do and when"
debug view.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import AgentEvent
from ..schemas import AgentEventOut

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=list[AgentEventOut])
async def list_events(
    limit: int = Query(default=100, ge=1, le=500),
    event_type: str | None = None,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Newest-first event log, optionally filtered by type/status."""
    stmt = select(AgentEvent)
    if event_type:
        stmt = stmt.where(AgentEvent.event_type == event_type)
    if status:
        stmt = stmt.where(AgentEvent.status == status)
    stmt = stmt.order_by(AgentEvent.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()
