"""Usage analytics — the learner's own historical data.

GET /api/analytics/summary?days=7 returns windowed study time, quiz
outcomes + answer latency, per-concept accuracy, and the FSRS retention
curve. Computed by agent/stats.window_summary — the same module the agent
side consumes, so what the user sees and what the planner sees can't drift.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent import stats
from ..db import get_session

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/summary")
async def summary(
    days: int = Query(default=7, ge=1, le=90),
    session: AsyncSession = Depends(get_session),
):
    return await stats.window_summary(session, days=days)
