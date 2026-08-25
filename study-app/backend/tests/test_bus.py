"""Event bus unit tests — dispatch, isolation, background handlers, logging."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import select

from app.db import SessionLocal
from app.events import bus
from app.models import AgentEvent


@dataclass
class Ping:
    value: str = ""


async def _rows(event_type: str) -> list[AgentEvent]:
    async with SessionLocal() as s:
        result = await s.execute(
            select(AgentEvent).where(AgentEvent.event_type == event_type)
        )
        return list(result.scalars().all())


async def test_inline_dispatch_order_and_isolation():
    """Handlers run in registration order; a failing handler doesn't stop the
    others, and every run (ok or failed) lands in the event log."""
    calls: list[str] = []

    @bus.on(Ping)
    async def first(event, session):
        calls.append("first")

    @bus.on(Ping)
    async def boom(event, session):
        raise RuntimeError("handler exploded")

    @bus.on(Ping)
    async def last(event, session):
        calls.append("last")

    await bus.publish(Ping(value="hi"))

    assert calls == ["first", "last"]  # boom skipped, dispatch continued

    rows = await _rows("Ping")
    by_handler = {r.handler: r for r in rows}
    assert set(by_handler) == {
        None,  # dispatch row
        "test_bus.first",
        "test_bus.boom",
        "test_bus.last",
    }
    assert by_handler[None].status == "ok"
    assert by_handler[None].payload == {"value": "hi"}
    assert by_handler["test_bus.first"].status == "ok"
    assert by_handler["test_bus.last"].status == "ok"
    assert by_handler["test_bus.boom"].status == "failed"
    assert "handler exploded" in (by_handler["test_bus.boom"].error or "")


async def test_background_handler_runs_as_tracked_task():
    """background=True handlers run as tasks; publish() returns immediately
    and drain() waits for them."""
    started = asyncio.Event()

    @bus.on(Ping, background=True)
    async def slow(event, session):
        started.set()

    await bus.publish(Ping(value="bg"))
    # Not asserted inline (that's the point of background) — but it must
    # complete promptly and be drained/logged.
    await asyncio.wait_for(started.wait(), timeout=2.0)
    await bus.drain()

    rows = await _rows("Ping")
    assert any(r.handler == "test_bus.slow" and r.status == "ok" for r in rows)


async def test_handler_writes_commit_with_ok_row():
    """A handler's DB writes and its 'ok' log row commit together."""

    @bus.on(Ping)
    async def writer(event, session):
        # A write on the handler's (bus-provided) session — no commit.
        from app.agent import memory as memory_store

        await memory_store.write_memory(
            session, "user", "", "ping_marker", event.value
        )

    await bus.publish(Ping(value="committed"))

    from app.agent import memory as memory_store

    async with SessionLocal() as s:
        marker = await memory_store.read_memory(s, "user", "", "ping_marker")
    assert marker == "committed"

    rows = await _rows("Ping")
    assert any(
        r.handler == "test_bus.writer" and r.status == "ok" for r in rows
    )


async def test_no_handlers_still_logs_dispatch():
    """Publishing an event nobody listens to still leaves a dispatch row."""

    @dataclass
    class Unheard:
        pass

    await bus.publish(Unheard())
    rows = await _rows("Unheard")
    assert len(rows) == 1
    assert rows[0].handler is None
    assert rows[0].status == "ok"
