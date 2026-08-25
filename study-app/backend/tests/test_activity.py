"""Activity telemetry tests — the capture spine, distillation, and profile
surface. Exercises POST /api/activity exactly as sendBeacon sends it
(text/plain body) and verifies the ledger + memory keys + event log.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from app.agent import behavior
from app.db import SessionLocal
from app.models import AgentEvent, UserActivity


async def test_activity_batch_lands_in_ledger_and_memory(client, db):
    batch = {
        "events": [
            {
                "type": "document.opened",
                "ts": "2026-08-14T20:15:00Z",
                "props": {"document_id": "doc-a1", "tab": "document"},
            },
            {
                "type": "document.closed",
                "ts": "2026-08-14T20:17:30Z",
                "props": {"document_id": "doc-a1", "dwell_secs": 150},
            },
            {
                "type": "tab.switched",
                "ts": "2026-08-14T20:15:10Z",
                "props": {"document_id": "doc-a1", "from": "document", "to": "quiz"},
            },
            {
                "type": "study.abandoned",
                "ts": "2026-08-14T20:15:20Z",
                "props": {"session_id": "s1", "completed": 3, "total": 10},
            },
            {
                "type": "zoom.changed",
                "ts": "2026-08-14T20:15:30Z",
                "props": {"document_id": "doc-a1", "scale": 1.4},
            },
        ]
    }
    resp = await client.post(
        "/api/activity",
        content=json.dumps(batch),
        headers={"Content-Type": "text/plain"},  # sendBeacon style
    )
    assert resp.status_code == 202
    assert resp.json()["accepted"] == 5

    # Ledger rows written.
    result = await db.execute(select(UserActivity).order_by(UserActivity.ts))
    rows = list(result.scalars().all())
    assert len(rows) == 5
    assert {r.type for r in rows} == {
        "document.opened",
        "document.closed",
        "tab.switched",
        "study.abandoned",
        "zoom.changed",
    }

    # Distilled memory keys.
    engagement = await behavior.get_engagement(db)
    assert engagement["actions_count"] == 5
    assert engagement["docs"]["doc-a1"]["views"] == 1
    assert engagement["docs"]["doc-a1"]["dwell_secs"] == 150
    assert engagement["total_dwell_secs"] == 150
    assert engagement["tab_switches"]["quiz"] == 1

    patterns = await behavior.get_study_patterns(db)
    assert patterns["sessions"]["abandoned"] == 1
    # Hour histogram from the client timestamps (all 20:xx UTC).
    assert patterns["hour_histogram"][20] == 5
    assert patterns["best_study_hour"] == 20

    # Both handlers logged ok on the bus.
    async with SessionLocal() as s:
        log_rows = (
            await s.execute(
                select(AgentEvent).where(AgentEvent.event_type == "ActivitiesLogged")
            )
        ).scalars().all()
    by_handler = {r.handler: r for r in log_rows}
    assert by_handler["activity.log_activities"].status == "ok"
    assert by_handler["activity.distill_engagement"].status == "ok"


async def test_activity_missing_ts_gets_server_time_and_bad_batches_drop(client, db):
    resp = await client.post(
        "/api/activity",
        content=json.dumps({"events": [{"type": "navigation.moved", "props": {"to": "home"}}]}),
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 202
    assert resp.json()["accepted"] == 1
    rows = (await db.execute(select(UserActivity))).scalars().all()
    assert rows[-1].ts is not None  # server filled the timestamp

    # Malformed body never 500s.
    resp = await client.post(
        "/api/activity", content=b"not json", headers={"Content-Type": "text/plain"}
    )
    assert resp.status_code == 202
    assert resp.json()["accepted"] == 0


async def test_profile_endpoint_includes_behavioral_fields(client, db):
    resp = await client.get("/api/memory/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert "insights" in body
    assert "patterns" in body
    assert "engagement" in body
    assert "slow_concepts" in body
