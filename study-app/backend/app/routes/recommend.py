"""Recommendation endpoint — thin shim over the strategy engine.

The heavy lifting lives in app/recommend/. This route just builds the context,
calls the engine, logs the impression for telemetry, and returns the response.
Also exposes the feedback endpoint for interaction tracking.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..recommend import build_context, engine

router = APIRouter(prefix="/api/recommend", tags=["recommend"])
logger = logging.getLogger(__name__)


@router.get("")
async def recommend(session: AsyncSession = Depends(get_session)):
    """Return the agent's recommendation for what to study next."""
    ctx = await build_context(session)
    response = engine.decide(ctx)

    # Log impression for telemetry, with the bandit's feature vector —
    # the context that produced this impression can't be reconstructed later.
    try:
        from ..recommend.bandit import LinUCBOptimizer
        from ..recommend.telemetry import log_impression

        features = LinUCBOptimizer().extract_features(ctx).tolist()
        await log_impression(session, response, features=features)
        await session.commit()
    except Exception:
        pass  # telemetry is best-effort

    return response


class FeedbackRequest(BaseModel):
    impression_id: str
    strategy_name: str
    action: str  # "clicked" | "dismissed" | "completed" | "abandoned"
    duration_secs: float | None = None


@router.post("/feedback")
async def feedback(
    req: FeedbackRequest, session: AsyncSession = Depends(get_session)
):
    """Log a user interaction with a recommendation (click/dismiss/complete).

    This feeds the telemetry loop that the LinUCB bandit uses to optimize
    strategy weights over time. Dismissals also feed the session store so
    the engine stops suggesting the dismissed strategy this session.
    """
    from ..recommend.session import record_dismissal
    from ..recommend.telemetry import log_interaction

    await log_interaction(
        session,
        impression_id=req.impression_id,
        strategy_name=req.strategy_name,
        action_type=req.action,
        duration_secs=req.duration_secs,
    )
    if req.action == "dismissed":
        await record_dismissal(session, req.strategy_name)

    # The route owns the transaction (telemetry stages, doesn't commit).
    await session.commit()
    return {"status": "ok"}
