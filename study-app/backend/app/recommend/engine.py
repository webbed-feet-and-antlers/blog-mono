"""RecommendationEngine — the strategy registry and decision loop.

The engine is intentionally simple: it iterates registered strategies, collects
their scores, and picks the top N. Adding a new tool = create a strategy +
register it. The engine never needs to change.

Design from the conversation's "Strategy & Registry Pattern":
  1. Each strategy evaluates itself against the UserContext (score 0.0-1.0).
  2. Soft overrides (FSRS urgency, active lecture) naturally score 0.90+.
  3. Fatigue filter adjusts scores based on cognitive load.
  4. Top result = primary; complementary categories = alternatives.
"""

from __future__ import annotations

import logging
import random
import uuid
from typing import Protocol, runtime_checkable

from .context import UserContext, RecommendationResult

logger = logging.getLogger(__name__)


@runtime_checkable
class ToolStrategy(Protocol):
    """Interface every study tool strategy implements.

    A strategy self-describes:
      - name: unique identifier (used for feature flags + telemetry)
      - category: "organize" | "learn" | "practice" | "onboarding"
      - evaluate: score this strategy against the context (0.0-1.0 or None)
    """
    name: str
    category: str

    def evaluate(self, ctx: UserContext) -> RecommendationResult | None:
        """Score this strategy. Return None if not applicable, or a
        RecommendationResult with score 0.0-1.0."""
        ...


# Fatigue penalties by cognitive load tier.
# High-load tools (quiz, practice tests) are penalized more when fatigued.
FATIGUE_PENALTIES = {
    "fresh": 0.0,       # No penalty when fresh
    "focused": 0.05,    # Slight penalty after 20+ min
    "fatigued": 0.20,   # Significant penalty after 50+ min
}

# Peak-hour boost — the mirror of the fatigue penalty. When it's near the
# learner's habitual study hour (study_patterns.best_study_hour), practice
# gets a small lift instead of a drag.
PEAK_HOUR_BOOST = 0.05


class RecommendationEngine:
    """The strategy registry. Holds all registered strategies and runs
    the decision loop."""

    def __init__(self):
        self._strategies: list[ToolStrategy] = []

    def register(self, strategy: ToolStrategy) -> None:
        """Register a strategy. Called at app startup."""
        self._strategies.append(strategy)
        logger.info("[recommend] registered strategy: %s", strategy.name)

    def decide(self, ctx: UserContext, top_n: int = 3) -> dict:
        """Run all strategies, score them, and return the recommendation response.

        Returns the same shape as the existing /api/recommend endpoint:
        { primary, alternatives, context, impression_id }
        """
        # Evaluate all enabled strategies.
        results: list[RecommendationResult] = []
        for strategy in self._strategies:
            if strategy.name not in ctx.enabled_features:
                continue
            try:
                result = strategy.evaluate(ctx)
            except Exception:
                logger.exception(
                    "[recommend] strategy %s raised", strategy.name
                )
                continue
            if result and result.score > 0:
                results.append(result)

        # Apply fatigue penalty to all results.
        if ctx.session:
            penalty = FATIGUE_PENALTIES.get(ctx.session.fatigue_level, 0.0)
            if penalty > 0:
                for r in results:
                    if r.category == "practice":
                        r.score = max(0.0, r.score - penalty)

        # Peak-hour boost — practice lifts slightly near the learner's
        # habitual study hour (the fatigue penalty's optimistic mirror).
        if ctx.is_peak_hour:
            for r in results:
                if r.category == "practice":
                    r.score = min(1.0, r.score + PEAK_HOUR_BOOST)

        # Apply dismissal penalty — if the user dismissed a tool this session,
        # drop its score significantly.
        if ctx.session and ctx.session.dismissed_tools:
            for r in results:
                if r.strategy_name in ctx.session.dismissed_tools:
                    r.score *= 0.1  # collapse to near-zero

        # Epsilon-greedy exploration: 10% chance to boost a random non-top
        # result (for the bandit to gather fresh telemetry).
        if len(results) > 1 and random.random() < 0.10:
            non_top = results[1:]
            if non_top:
                explorer = random.choice(non_top)
                explorer.score += 0.5  # boost to likely-primary

        # Sort by score descending.
        results.sort(key=lambda r: r.score, reverse=True)

        # Pick primary + alternatives (complementary categories).
        primary = results[0] if results else None
        alternatives = self._pick_alternatives(results[1:] if primary else results, primary)

        # Build the context summary for the frontend.
        context_summary = {
            "due_count": ctx.due_count,
            "learner_level": ctx.profile.get("learner_level", "unknown"),
            "total_concepts": ctx.total_concepts,
            "mastered_count": ctx.mastered_count,
            "welcome_back": ctx.welcome_back,
            "total_quizzes": (ctx.profile.get("stats") or {}).get("total_quizzes", 0),
        }

        impression_id = uuid.uuid4().hex[:16]

        return {
            "primary": primary.to_dict() if primary else None,
            "alternatives": [a.to_dict() for a in alternatives[:3]],
            "context": context_summary,
            "impression_id": impression_id,
        }

    def _pick_alternatives(
        self,
        candidates: list[RecommendationResult],
        primary: RecommendationResult | None,
    ) -> list[RecommendationResult]:
        """Pick alternatives from complementary categories.

        Avoid suggesting the same category as the primary. Pick the top
        result from each different category.
        """
        if not candidates:
            return []

        seen_categories: set[str] = set()
        if primary:
            seen_categories.add(primary.category)

        alts: list[RecommendationResult] = []
        for result in candidates:
            if result.category not in seen_categories:
                alts.append(result)
                seen_categories.add(result.category)
            if len(alts) >= 2:
                break

        # If we didn't find enough complementary ones, fill with any remaining.
        if len(alts) < 2:
            for result in candidates:
                if result not in alts:
                    alts.append(result)
                if len(alts) >= 2:
                    break

        return alts


# Module-level singleton — the app's recommendation engine.
# Strategies are registered here at import time (see strategies/__init__.py).
engine = RecommendationEngine()
