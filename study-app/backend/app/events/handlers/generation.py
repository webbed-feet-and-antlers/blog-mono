"""Reactions to generation — the agent produced a ContentItem.

The user-facing generate routes publish GenerationCompleted after their
commit, so recommendation chaining/fatigue finally sees generation as an
activity (the old record_action docstring claimed this happened; it never
did). Auto/proactive generation deliberately does NOT publish — those aren't
user actions.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ...recommend.session import record_action
from .. import bus
from ..domain import GenerationCompleted

logger = logging.getLogger(__name__)


@bus.on(GenerationCompleted)
async def record_activity(event: GenerationCompleted, session: AsyncSession) -> None:
    """Record the generation as a completed user action."""
    await record_action(session, "generate", event.document_id)
