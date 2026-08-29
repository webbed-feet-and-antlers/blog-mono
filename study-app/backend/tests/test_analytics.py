"""Analytics summary endpoint — the user-facing windowed stats.

Seeds the ledger + quiz attempts directly (bypassing the event bus) and
asserts the aggregates: daily study minutes, quiz outcomes, per-concept
window accuracy, latency, streak. The suite shares one DB file, so the
seeding test cleans up after itself (the empty-window test depends on it).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.models import ContentItem, Document, QuizAttempt, UserActivity
from tests.conftest import make_quiz


@pytest.fixture(autouse=True)
async def _clean_ledger(db):
    """Deterministic state: the suite shares one DB file, and earlier
    files' ledger rows land inside our 7-day windows. Wipe the tables
    analytics reads before AND after each test here so both this file's
    tests and later files start from a known state."""
    await _wipe(db)
    yield
    await _wipe(db)


async def _wipe(db):
    await db.execute(delete(UserActivity))
    await db.execute(delete(QuizAttempt))
    await db.commit()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


async def _cleanup(db):
    await db.execute(delete(ContentItem).where(ContentItem.id == "quiz-1"))
    await db.execute(delete(Document).where(Document.id == "doc-quiz"))
    await db.commit()


async def _seed(db):
    now = datetime.now(timezone.utc)
    doc, quiz = make_quiz()
    db.add(doc)
    db.add(quiz)

    # Two quiz attempts: 3 days ago (2/2 correct), today (1/2).
    questions = quiz.content["questions"]
    db.add(
        QuizAttempt(
            id="attempt-old",
            content_id=quiz.id,
            user_id="",
            answers={"q1": questions[0]["answer_idx"], "q2": questions[1]["answer_idx"]},
            score=1.0,
            correct_count=2,
            total_count=2,
            taken_at=now - timedelta(days=3),
        )
    )
    db.add(
        QuizAttempt(
            id="attempt-new",
            content_id=quiz.id,
            user_id="",
            answers={"q1": questions[0]["answer_idx"], "q2": 0},  # q2 wrong
            score=0.5,
            correct_count=1,
            total_count=2,
            taken_at=now - timedelta(minutes=30),
        )
    )

    events = [
        # 12 minutes of document dwell today, 30 yesterday.
        UserActivity(id="a1", user_id="", ts=now - timedelta(minutes=90), type="document.closed",
                     props={"dwell_secs": 300, "document_id": doc.id}),
        UserActivity(id="a2", user_id="", ts=now - timedelta(minutes=60), type="document.closed",
                     props={"dwell_secs": 420, "document_id": doc.id}),
        UserActivity(id="a3", user_id="", ts=now - timedelta(days=1), type="document.closed",
                     props={"dwell_secs": 1800, "document_id": doc.id}),
        # Per-question latencies (seconds).
        UserActivity(id="a4", user_id="", ts=now - timedelta(minutes=30), type="quiz.answered",
                     props={"question_id": "q1", "latency_secs": 20}),
        UserActivity(id="a5", user_id="", ts=now - timedelta(minutes=29), type="quiz.answered",
                     props={"question_id": "q2", "latency_secs": 40}),
        # Distractor type for the histogram.
        UserActivity(id="a6", user_id="", ts=now - timedelta(days=8), type="document.closed",
                     props={"dwell_secs": 9999}),  # outside the 7-day window
        UserActivity(id="a7", user_id="", ts=now - timedelta(days=2), type="navigation.moved",
                     props={}),
    ]
    db.add_all(events)
    await db.commit()
    return quiz


async def test_summary_aggregates(client, db):
    quiz = await _seed(db)

    res = await client.get("/api/analytics/summary", params={"days": 7})
    assert res.status_code == 200
    body = res.json()

    assert body["days"] == 7

    study = body["study"]
    assert study["total_minutes"] == 42.0  # 5 + 7 + 30, the 8-day-old event excluded
    assert study["active_days"] == 2
    assert study["streak_days"] == 2  # yesterday + today
    today_row = study["minutes_by_day"][-1]
    assert today_row["minutes"] == 12.0
    assert len(study["minutes_by_day"]) == 7

    quizzes = body["quizzes"]
    assert quizzes["count"] == 2
    assert quizzes["avg_score"] == 0.75
    assert quizzes["questions_answered"] == 2
    assert quizzes["avg_question_latency_secs"] == 30.0

    # Per-concept accuracy within the window: q1 (Photosynthesis) 2/2,
    # q2 (Calvin cycle) 1/2.
    concepts = {c["concept"]: c for c in body["concepts"]}
    assert concepts["Photosynthesis"]["accuracy"] == 1.0
    assert concepts["Calvin cycle"]["accuracy"] == 0.5

    hist = {e["type"]: e["count"] for e in body["top_activities"]}
    assert hist["document.closed"] == 3
    assert hist["quiz.answered"] == 2
    assert hist["navigation.moved"] == 1

    # Retention curve always renders 5 buckets (sparse here).
    assert len(body["retention_curve"]) == 5

    await _cleanup(db)


async def test_summary_empty_window(client, db):
    res = await client.get("/api/analytics/summary")
    assert res.status_code == 200
    body = res.json()
    assert body["study"]["total_minutes"] == 0.0
    assert body["quizzes"]["count"] == 0
    assert body["quizzes"]["avg_score"] is None
    assert body["concepts"] == []
