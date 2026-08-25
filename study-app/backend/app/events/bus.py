"""In-process domain event bus — the spine for all automatic behaviors.

Routes do their core write, commit it, then `await bus.publish(Event(...))`.
Everything the app does "automatically" lives in a handler subscribed to an
event (see app/events/handlers/), instead of being wired inline into routes.

Delivery semantics:
  - publish() is POST-COMMIT: the core write (quiz attempt, review, upload)
    is already durable when reactions run. A failing handler can never roll
    back or 500 the user's action — it is logged and skipped.
  - Inline handlers (default) are awaited inside publish(), each in its own
    session with its own commit. Fast DB reactions finish before the HTTP
    response returns, so side effects are visible immediately — same as the
    old inline code.
  - background=True handlers are spawned as tracked asyncio.Tasks for slow
    work (LLM calls). Task references are held + discarded on completion so
    exceptions are always logged, never silently dropped.

Observability:
  - Every publish writes a "dispatch" row to agent_events (handler=None) so
    in-flight background chains are visible immediately.
  - Every handler run writes an "ok" row on the SAME session as the
    handler's writes — the row commits iff the handler's writes commit.
    Failures get a "failed" row with the error, written in a fresh session.

Rules for handlers:
  - Signature: `async def handler(event, session) -> None`. Don't commit —
    the bus commits (and adds the log row) when the handler returns.
  - Exception: mid-flow commits are allowed when the handler needs durable
    milestones (e.g. rename-before-analysis); the bus's final commit is then
    a no-op.
  - If a handler publishes a nested event, commit its own writes FIRST —
    the nested dispatch row writes from a separate session and must not
    collide with an open write transaction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import AgentEvent

logger = logging.getLogger(__name__)

Handler = Callable[..., Any]

_MAX_PAYLOAD_CHARS = 8_000  # keep the log table light (analyses can be big)
_MAX_ERROR_CHARS = 2_000


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type, list[tuple[Handler, bool]]] = {}
        # Background task registry — keeps references so exceptions surface
        # (fire-and-forget tasks with no reference are silently swallowed).
        self._tasks: set[asyncio.Task] = set()

    # --- Registration ------------------------------------------------------

    def on(self, *event_types: type, background: bool = False) -> Callable[[Handler], Handler]:
        """Register a handler for one or more event types.

        background=True spawns the handler as a tracked task instead of
        awaiting it inside publish() — for slow work (LLM calls).
        """

        def decorator(fn: Handler) -> Handler:
            for event_type in event_types:
                self._handlers.setdefault(event_type, []).append((fn, background))
                logger.debug(
                    "[events] registered %s%s -> %s.%s",
                    getattr(event_type, "__name__", event_type),
                    " (background)" if background else "",
                    fn.__module__,
                    fn.__name__,
                )
            return fn

        return decorator

    # --- Dispatch ----------------------------------------------------------

    async def publish(self, event: Any) -> None:
        """Dispatch an event to all registered handlers.

        Inline handlers run to completion (each isolated); background
        handlers are scheduled and publish() returns immediately.
        """
        event_name = type(event).__name__
        await self._log_dispatch(event)

        entries = self._handlers.get(type(event), [])
        if not entries:
            logger.debug("[events] %s published — no handlers", event_name)
            return

        for fn, background in entries:
            if background:
                task = asyncio.create_task(
                    self._run_handler(fn, event), name=f"{event_name}:{fn.__name__}"
                )
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            else:
                await self._run_handler(fn, event)

    async def drain(self) -> None:
        """Wait for all in-flight background handlers to finish (tests/shutdown)."""
        tasks = [t for t in self._tasks if not t.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # --- Internals ---------------------------------------------------------

    async def _run_handler(self, fn: Handler, event: Any) -> None:
        label = f"{fn.__module__.rsplit('.', 1)[-1]}.{fn.__name__}"
        event_name = type(event).__name__
        try:
            async with SessionLocal() as session:
                await fn(event, session)
                # Same transaction as the handler's writes: the "ok" row only
                # commits if the handler's writes commit.
                session.add(_event_row(event, label, "ok", None))
                await session.commit()
        except Exception as exc:
            logger.exception(
                "[events] handler %s failed for %s", label, event_name
            )
            await self._log_failure(event, label, exc)

    async def _log_dispatch(self, event: Any) -> None:
        """Best-effort dispatch row — makes the event visible immediately,
        even while its background handlers are still running."""
        try:
            async with SessionLocal() as session:
                session.add(_event_row(event, None, "ok", None))
                await session.commit()
        except Exception:
            logger.exception(
                "[events] failed to log dispatch row for %s", type(event).__name__
            )

    async def _log_failure(self, event: Any, label: str, exc: Exception) -> None:
        try:
            async with SessionLocal() as session:
                session.add(_event_row(event, label, "failed", str(exc)))
                await session.commit()
        except Exception:
            logger.error("[events] failed to record handler failure for %s", label)


def _event_row(
    event: Any, handler: str | None, status: str, error: str | None
) -> AgentEvent:
    return AgentEvent(
        id=uuid.uuid4().hex[:12],
        event_type=type(event).__name__,
        handler=handler,
        status=status,
        payload=_safe_payload(event),
        error=(str(error)[:_MAX_ERROR_CHARS] if error else None),
    )


def _safe_payload(event: Any) -> dict:
    """Serialize an event to a JSON-safe, size-capped payload dict."""
    try:
        data = asdict(event) if is_dataclass(event) else {"event": repr(event)}
        encoded = json.dumps(data, default=str)
        if len(encoded) > _MAX_PAYLOAD_CHARS:
            # Truncate and re-wrap so the row stays loadable.
            return {"_truncated": True, "preview": encoded[:_MAX_PAYLOAD_CHARS]}
        return data
    except Exception:
        return {"_unserializable": True}


# Module-level singleton — imported everywhere as `from ..events import bus`.
bus = EventBus()
