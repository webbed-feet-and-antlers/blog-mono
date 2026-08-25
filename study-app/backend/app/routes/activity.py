"""Activity telemetry route — the frontend's fire-and-forget tracking sink.

The frontend buffers interaction events and flushes them here in batches
(fetch keepalive, or navigator.sendBeacon on tab close). sendBeacon posts
with Content-Type text/plain, so this reads the raw body instead of relying
on FastAPI's JSON parsing — no CORS preflight, no drop.

Events land on the bus as ActivitiesLogged; handlers write the append-only
user_activities ledger and distill engagement/study-pattern memory.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..events import bus
from ..events.domain import ActivitiesLogged, ActivityEntry
from ..schemas import ActivityBatchIn

router = APIRouter(prefix="/api/activity", tags=["activity"])
logger = logging.getLogger(__name__)

MAX_EVENTS_PER_BATCH = 100
MAX_PROPS_CHARS = 2048


@router.post("", status_code=202)
async def track_activities(request: Request):
    raw = await request.body()
    try:
        payload = ActivityBatchIn.model_validate(json.loads(raw or b"{}"))
    except (json.JSONDecodeError, ValidationError) as exc:
        # Telemetry must never break the client — accept and drop.
        logger.warning("[activity] dropped malformed batch: %s", exc)
        return JSONResponse({"accepted": 0}, status_code=202)

    now_iso = None
    entries: list[ActivityEntry] = []
    for event in payload.events[:MAX_EVENTS_PER_BATCH]:
        etype = (event.type or "").strip()
        if not etype:
            continue
        ts = (event.ts or "").strip()
        if not ts:
            if now_iso is None:
                from datetime import datetime, timezone

                now_iso = datetime.now(timezone.utc).isoformat()
            ts = now_iso
        props = event.props if isinstance(event.props, dict) else {}
        if len(json.dumps(props, default=str)) > MAX_PROPS_CHARS:
            props = {"_truncated": True}
        entries.append(ActivityEntry(type=etype, ts=ts, props=props))

    if entries:
        await bus.publish(ActivitiesLogged(entries=entries))
    return JSONResponse({"accepted": len(entries)}, status_code=202)
