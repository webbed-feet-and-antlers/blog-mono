"""Session tracking — records the user's recent actions for action chaining
and fatigue-aware pacing.

Session lives in agent_memory (scope="user", key="session"). Actions are
appended with timestamps. The session auto-expires after 2 hours of inactivity
(a new session starts fresh). This powers:
  - Action chaining: "you just did flashcards → try a quiz"
  - Fatigue: "you've been studying 50 min → switch to lighter tools"
  - Dismissal: "you dismissed this recommendation → don't show it again"
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.memory import blob_lock, read_memory, write_memory

logger = logging.getLogger(__name__)

SESSION_KEY = "session"
SESSION_TIMEOUT_SECS = 2 * 3600  # 2 hours


async def record_action(
    session: AsyncSession,
    tool: str,
    doc_id: str | None = None,
) -> None:
    """Record that the user completed an action (quiz, flashcard review, etc).

    Called from the event handlers (app/events/handlers/) after the action
    completes.
    """
    now = datetime.now(timezone.utc).isoformat()
    async with blob_lock:
        data = await read_memory(session, "user", "", SESSION_KEY)

        if not isinstance(data, dict):
            data = {"actions": [], "started_at": now, "dismissed_tools": []}

        actions = data.get("actions") or []

        # Check if the session has expired — start a new one if so.
        if actions:
            try:
                last_ts = datetime.fromisoformat(actions[-1]["ts"])
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - last_ts).total_seconds() > SESSION_TIMEOUT_SECS:
                    # Session expired — start fresh.
                    actions = []
                    data["started_at"] = now
                    data["dismissed_tools"] = []
            except (KeyError, ValueError):
                pass

        actions.append({"tool": tool, "doc_id": doc_id, "ts": now})
        # Keep last 20 actions.
        data["actions"] = actions[-20:]
        if not data.get("started_at"):
            data["started_at"] = now

        await write_memory(session, "user", "", SESSION_KEY, data)


async def record_dismissal(
    session: AsyncSession,
    strategy_name: str,
) -> None:
    """Record that the user dismissed a recommendation for this session."""
    async with blob_lock:
        data = await read_memory(session, "user", "", SESSION_KEY)
        if not isinstance(data, dict):
            data = {"actions": [], "started_at": datetime.now(timezone.utc).isoformat(), "dismissed_tools": []}

        dismissed = set(data.get("dismissed_tools") or [])
        dismissed.add(strategy_name)
        data["dismissed_tools"] = list(dismissed)

        await write_memory(session, "user", "", SESSION_KEY, data)
