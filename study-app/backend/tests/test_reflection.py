"""Reflection tests — the LLM narrative layer, cooldown-gated.

The LLM call is monkeypatched; these tests verify the plumbing: grounding
packet, insights write, cooldown behavior, force bypass.
"""

from __future__ import annotations

import json

from app.agent import reflection
from app.agent import memory as memory_store
from app.db import SessionLocal
from app.models import UserActivity


async def _seed_activities(n: int, db) -> None:
    import uuid
    from datetime import datetime, timezone

    for i in range(n):
        db.add(
            UserActivity(
                id=uuid.uuid4().hex[:12],
                ts=datetime.now(timezone.utc),
                type="document.opened" if i % 2 else "tab.switched",
                props={"document_id": f"doc-r{i % 3}", "tab": "quiz"},
            )
        )
    await db.commit()


class _FakeLLM:
    """Stands in for reflection.chat_json — captures the prompt."""

    def __init__(self, result):
        self.result = result
        self.calls: list[list[dict]] = []

    async def __call__(self, messages, **kwargs):
        self.calls.append(messages)
        return self.result


async def test_reflection_writes_insights(client, db, monkeypatch):
    from sqlalchemy import func, select

    from app.models import UserActivity

    before = int(await db.scalar(select(func.count(UserActivity.id))) or 0)
    await _seed_activities(30, db)
    fake = _FakeLLM(
        {
            "summary": "Reviews flashcards more than quizzes; active late evenings.",
            "traits": ["evening studier", "flashcard-leaning"],
            "habits": "Studies in short bursts after 8pm.",
        }
    )
    monkeypatch.setattr(reflection, "chat_json", fake)

    result = await reflection.reflect_on_learner(db, force=True)
    assert result["status"] == "updated"
    await db.commit()

    insights = await reflection.get_learner_insights(db)
    assert insights is not None
    assert insights["summary"].startswith("Reviews flashcards")
    assert insights["traits"] == ["evening studier", "flashcard-leaning"]
    assert insights["activities_seen"] == before + 30

    # The prompt was grounded: system prompt + rendered packet with signals.
    assert len(fake.calls) == 1
    system = fake.calls[0][0]["content"]
    assert "Base EVERY claim" in system
    user_prompt = fake.calls[0][1]["content"]
    assert "document.opened" in user_prompt  # activity-type counts
    assert "hour_histogram_utc" in user_prompt


async def test_reflection_cooldown_respected(client, db, monkeypatch):
    await _seed_activities(10, db)  # fewer than MIN_NEW_ACTIVITIES (25)

    # First reflection (forced) establishes the baseline.
    fake = _FakeLLM({"summary": "s", "traits": ["t"], "habits": "h"})
    monkeypatch.setattr(reflection, "chat_json", fake)
    await reflection.reflect_on_learner(db, force=True)
    await db.commit()

    # Force=False + only 5 more activities → skipped, no LLM call.
    await _seed_activities(5, db)
    result = await reflection.reflect_on_learner(db, force=False)
    assert result["status"] == "skipped"
    assert "new activities" in result["reason"]
    assert len(fake.calls) == 1  # no second call

    # The cooldown endpoint reports skip without force.
    resp = await client.post("/api/memory/reflect?force=false")
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"


async def test_reflection_llm_failure_never_raises(client, db, monkeypatch):
    """A failed reflection reports an error status but keeps prior insights."""

    async def boom(messages, **kwargs):
        raise RuntimeError("LLM exploded")

    monkeypatch.setattr(reflection, "chat_json", boom)
    result = await reflection.reflect_on_learner(db, force=True)
    assert result["status"] == "error"
    assert "LLM exploded" in result["reason"]
