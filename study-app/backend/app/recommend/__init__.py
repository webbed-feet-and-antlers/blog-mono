"""Recommendation engine — Strategy & Registry pattern.

A plugin-based recommendation system where each study tool self-describes its
priority via a ToolStrategy interface. The engine iterates registered strategies,
scores them against a unified UserContext, and returns the next-best-action.

Adding a tool = create a strategy file + register it. Removing = unregister.
The core engine never changes.
"""

from .context import UserContext, RecommendationResult, build_context
from .engine import RecommendationEngine, ToolStrategy, engine
from .strategies import register_all

# Register all default strategies on import.
register_all(engine)

__all__ = [
    "UserContext",
    "RecommendationResult",
    "build_context",
    "RecommendationEngine",
    "ToolStrategy",
    "engine",
    "register_all",
]
