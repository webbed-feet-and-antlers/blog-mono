"""Telemetry — impression and interaction tracking for the recommendation engine.

Logs when recommendations are shown (impressions) and when users interact with
them (clicks, dismissals, completions). This data feeds the LinUCB bandit for
automatic weight optimization.

Phase 3 of the recommendation system build-out.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def log_impression(session: AsyncSession, response: dict) -> None:
    """Log a recommendation impression — one event per shown recommendation.

    Called from the /api/recommend endpoint after the engine decides.
    Best-effort: failures are swallowed so they don't break recommendations.
    """
    try:
        from ..models import RecommendationEvent

        impression_id = response.get("impression_id", uuid.uuid4().hex[:16])

        # Log primary.
        primary = response.get("primary")
        if primary:
            event = RecommendationEvent(
                id=uuid.uuid4().hex[:12],
                impression_id=impression_id,
                strategy_name=primary.get("strategy_name", "unknown"),
                action=primary.get("action", ""),
                document_id=primary.get("document_id"),
                score=primary.get("score", 0.0),
                rank=1,
                event_type="impression",
                reward=None,
                context_snapshot={},
            )
            session.add(event)

        # Log alternatives.
        for i, alt in enumerate(response.get("alternatives", []), start=2):
            event = RecommendationEvent(
                id=uuid.uuid4().hex[:12],
                impression_id=impression_id,
                strategy_name=alt.get("strategy_name", "unknown"),
                action=alt.get("action", ""),
                document_id=alt.get("document_id"),
                score=alt.get("score", 0.0),
                rank=i,
                event_type="impression",
                reward=None,
                context_snapshot={},
            )
            session.add(event)
    except Exception:
        logger.debug("[telemetry] log_impression failed (table may not exist yet)")


def calculate_reward(action_type: str, duration_secs: float | None = None) -> float:
    """Map user interaction to a reward score.

    Completed = 1.0 (user accepted and finished the task).
    Clicked = 0.4 (curiosity, but may have bounced).
    Dismissed = -0.5 (explicit refusal).
    Abandoned = -0.1 (ignored the card entirely).
    """
    if action_type == "completed":
        return 1.0
    elif action_type == "clicked":
        if duration_secs is not None and duration_secs < 15:
            return 0.0  # bounce / accidental click
        return 0.4
    elif action_type == "dismissed":
        return -0.5
    elif action_type == "abandoned":
        return -0.1
    return 0.0


async def log_interaction(
    session: AsyncSession,
    impression_id: str,
    strategy_name: str,
    action_type: str,
    duration_secs: float | None = None,
) -> None:
    """Log a user interaction with a recommendation (click/dismiss/complete)."""
    try:
        from ..models import RecommendationEvent
        from sqlalchemy import select

        # Find the original impression to attach the reward.
        result = await session.execute(
            select(RecommendationEvent)
            .where(
                RecommendationEvent.impression_id == impression_id,
                RecommendationEvent.strategy_name == strategy_name,
                RecommendationEvent.event_type == "impression",
            )
            .limit(1)
        )
        impression = result.scalars().first()

        reward = calculate_reward(action_type, duration_secs)

        # Update the impression's reward.
        if impression:
            impression.reward = reward

        # Also create an interaction event.
        event = RecommendationEvent(
            id=uuid.uuid4().hex[:12],
            impression_id=impression_id,
            strategy_name=strategy_name,
            action=impression.action if impression else "",
            document_id=impression.document_id if impression else None,
            score=impression.score if impression else 0.0,
            rank=impression.rank if impression else 0,
            event_type=action_type,
            reward=reward,
            context_snapshot={"duration_secs": duration_secs} if duration_secs else {},
        )
        session.add(event)
        await session.commit()
    except Exception:
        logger.debug("[telemetry] log_interaction failed")
