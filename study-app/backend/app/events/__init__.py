"""In-process domain event bus.

Usage from routes:
    from ..events import bus
    from ..events.domain import QuizAttempted
    ...
    await session.commit()          # core write is durable
    await bus.publish(QuizAttempted(...))   # automatic reactions run

Handlers live in app/events/handlers/ and are registered at import time
(main.py imports that package once).
"""

from __future__ import annotations

from . import domain
from .bus import EventBus, bus

__all__ = ["EventBus", "bus", "domain"]
