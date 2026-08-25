"""LinUCB contextual bandit — optimizes strategy weights from telemetry.

Each strategy maintains a weight vector W learned from (feature_vector, reward)
pairs via ridge regression. The bandit balances exploitation (using learned
weights) with exploration (trying new strategies to gather fresh telemetry).

This runs as a periodic background job (every 6 hours via the proactive loop).
The learned weights are stored in agent_memory and read by the engine on each
decide() call.

Safety guardrails (from the design doc):
  - Weight clamping: [-1.0, +2.0]
  - Epsilon-greedy: 90% exploit, 10% explore (handled in engine.py)
  - Onboarding strategy is immune (always fires first for new users)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.memory import read_memory, write_memory
from ..models import RecommendationEvent
from .context import UserContext

logger = logging.getLogger(__name__)

BANDIT_KEY = "bandit_weights"
NUM_FEATURES = 6  # bias + 5 context features (see extract_features)
WEIGHT_CLAMP_MIN = -1.0
WEIGHT_CLAMP_MAX = 2.0


class LinUCBOptimizer:
    """Per-strategy contextual bandit using LinUCB (Linear Upper Confidence Bound).

    For each strategy, maintains:
      A: covariance matrix (NUM_FEATURES x NUM_FEATURES), init = identity
      b: response vector (NUM_FEATURES,), init = zeros
      weights: W = A^(-1) * b

    Updated via ridge regression on aggregated telemetry.
    """

    def extract_features(self, ctx: UserContext) -> np.ndarray:
        """Normalize context to a numeric feature vector (0.0 to 1.0).

        Features:
          [0] bias (always 1.0)
          [1] fsrs_urgency: due_count / 50, clamped to 1.0
          [2] fatigue: duration_mins / 90, clamped to 1.0
          [3] chaining: 1.0 if last action was a different practice tool, else 0
          [4] exam_urgency: (30 - days_to_exam) / 30, or 0 if no exam
          [5] mastery_gap: 1.0 - (mastered_count / total_concepts), or 0
        """
        fsrs_urgency = min(ctx.due_count / 50, 1.0) if ctx.due_count else 0.0

        fatigue = 0.0
        if ctx.session:
            fatigue = min(ctx.session.duration_mins / 90, 1.0)

        chaining = 0.0
        if ctx.session and ctx.session.actions:
            chaining = 1.0

        # Exam urgency — days to the nearest upcoming exam (module exam_date),
        # full urgency on the day, zero from 30 days out.
        exam_urgency = 0.0
        if ctx.days_to_exam is not None:
            exam_urgency = max(0.0, min((30 - ctx.days_to_exam) / 30, 1.0))

        mastery_gap = 0.0
        if ctx.total_concepts > 0:
            mastery_gap = 1.0 - (ctx.mastered_count / ctx.total_concepts)

        return np.array([1.0, fsrs_urgency, fatigue, chaining, exam_urgency, mastery_gap])

    async def update_weights(self, session: AsyncSession) -> dict[str, float]:
        """Load recent telemetry, update each strategy's weights via ridge regression.

        Returns a summary of strategy_name → learned_weight_magnitude.
        """
        # Load all strategies' current parameters.
        params = await self._load_all_params(session)

        # Load recent interaction events with rewards.
        result = await session.execute(
            select(RecommendationEvent)
            .where(RecommendationEvent.event_type == "clicked")
            .where(RecommendationEvent.reward.isnot(None))
            .order_by(RecommendationEvent.created_at.desc())
            .limit(500)
        )
        events = result.scalars().all()

        if not events:
            logger.info("[bandit] no telemetry to update from yet")
            return {}

        # Group events by strategy and accumulate ridge regression updates.
        updates: dict[str, list[tuple[np.ndarray, float]]] = {}
        for event in events:
            # Prefer the feature vector stored at impression time (carried
            # onto interaction events by log_interaction). Legacy events
            # predate feature storage — fall back to the score proxy.
            stored = (event.context_snapshot or {}).get("features")
            if isinstance(stored, list) and len(stored) == NUM_FEATURES:
                features = np.array(stored, dtype=float)
            else:
                features = np.zeros(NUM_FEATURES)
                features[0] = 1.0  # bias
                features[1] = min(event.score, 1.0)  # score as urgency proxy

            strategy = event.strategy_name
            if strategy not in updates:
                updates[strategy] = []
            updates[strategy].append((features, event.reward or 0.0))

        # Update each strategy's parameters.
        summary = {}
        for strategy_name, samples in updates.items():
            if strategy_name not in params:
                params[strategy_name] = self._init_params()

            A = np.array(params[strategy_name]["A"])
            b = np.array(params[strategy_name]["b"])

            for features, reward in samples:
                # Rank-1 update: A = A + x * x^T
                A += np.outer(features, features)
                # b = b + r * x
                b += reward * features

            # Solve W = A^(-1) * b
            try:
                W = np.linalg.solve(A, b)
                # Clamp weights.
                W = np.clip(W, WEIGHT_CLAMP_MIN, WEIGHT_CLAMP_MAX)
            except np.linalg.LinAlgError:
                logger.warning("[bandit] singular matrix for %s, skipping", strategy_name)
                continue

            params[strategy_name]["A"] = A.tolist()
            params[strategy_name]["b"] = b.tolist()
            params[strategy_name]["weights"] = W.tolist()
            summary[strategy_name] = float(np.linalg.norm(W))

        await write_memory(session, "user", "", BANDIT_KEY, params)
        logger.info(
            "[bandit] updated weights for %d strategies from %d events",
            len(updates),
            len(events),
        )
        return summary

    async def get_weights(self, session: AsyncSession, strategy_name: str) -> list[float]:
        """Load learned weights for a strategy (or default zeros)."""
        params = await self._load_all_params(session)
        p = params.get(strategy_name)
        if p and "weights" in p:
            return p["weights"]
        return [0.0] * NUM_FEATURES

    async def _load_all_params(self, session: AsyncSession) -> dict:
        """Load all strategy parameters from agent_memory."""
        val = await read_memory(session, "user", "", BANDIT_KEY)
        return val if isinstance(val, dict) else {}

    def _init_params(self) -> dict:
        """Initialize LinUCB parameters: A = identity, b = zeros."""
        return {
            "A": np.eye(NUM_FEATURES).tolist(),
            "b": np.zeros(NUM_FEATURES).tolist(),
            "weights": np.zeros(NUM_FEATURES).tolist(),
        }


async def run_bandit_update() -> None:
    """Background job: update strategy weights from telemetry.

    Called from the proactive loop on a longer interval.
    """
    from ..db import SessionLocal

    try:
        async with SessionLocal() as session:
            optimizer = LinUCBOptimizer()
            summary = await optimizer.update_weights(session)
            await session.commit()
            if summary:
                logger.info("[bandit] weight update complete: %s", summary)
    except Exception:
        logger.exception("[bandit] update failed")
