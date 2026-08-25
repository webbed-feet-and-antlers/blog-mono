"""Reactions to frontend activity telemetry.

Two independent handlers:
  - log_activities      — the append-only user_activities ledger
  - distill_engagement  — deterministic aggregates into engagement +
                          study_patterns memory keys (agent/behavior.py)

The ledger is the raw behavioral memory; the distillation is what prompts
and the understanding panel read. Neither ever blocks the other.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...agent import behavior
from ...models import UserActivity
from .. import bus
from ..domain import ActivitiesLogged

logger = logging.getLogger(__name__)

# POC retention: keep the newest N rows, prune the rest during logging.
MAX_LEDGER_ROWS = 5_000


@bus.on(ActivitiesLogged)
async def log_activities(event: ActivitiesLogged, session: AsyncSession) -> None:
    """Append the batch to the behavior ledger (with light retention)."""
    rows = []
    for entry in event.entries:
        ts = _parse_ts(entry.ts)
        rows.append(
            UserActivity(
                id=uuid.uuid4().hex[:12],
                ts=ts,
                type=entry.type,
                props=entry.props,
            )
        )
    session.add_all(rows)
    await session.flush()

    # Prune oldest rows past the retention cap (cheap count + delete).
    count = await session.scalar(select(func.count(UserActivity.id)))
    if count and count > MAX_LEDGER_ROWS:
        overflow = count - MAX_LEDGER_ROWS
        cutoff = await session.scalar(
            select(UserActivity.ts)
            .order_by(UserActivity.ts.asc())
            .offset(overflow - 1)
            .limit(1)
        )
        if cutoff is not None:
            await session.execute(
                delete(UserActivity).where(UserActivity.ts <= cutoff)
            )
            logger.info("[activity] pruned %d old ledger rows", overflow)


@bus.on(ActivitiesLogged)
async def distill_engagement(event: ActivitiesLogged, session: AsyncSession) -> None:
    """Fold the batch into the engagement + study-pattern memory keys."""
    await behavior.distill_activities(session, event.entries)


def _parse_ts(ts: str) -> datetime:
    try:
        dt = datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
