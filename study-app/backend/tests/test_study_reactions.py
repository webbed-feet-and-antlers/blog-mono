"""End-to-end tests for the study reactions — quiz/flashcard/study submits
publish events, and the handlers (mastery, profile, session tracking, weak
topics) fire post-commit. No LLM is involved on these paths.
"""

from __future__ import annotations

from sqlalchemy import select

from app.agent import memory as memory_store
from app.config import settings
from app.models import AgentEvent, ContentItem, Document
from tests.conftest import make_quiz


async def _handler_rows(event_type: str) -> dict[str | None, AgentEvent]:
    from app.db import SessionLocal

    async with SessionLocal() as s:
        result = await s.execute(
            select(AgentEvent).where(AgentEvent.event_type == event_type)
        )
        return {r.handler: r for r in result.scalars().all()}


async def test_quiz_attempt_runs_all_reactions(client, db):
    doc, quiz = make_quiz(doc_id="doc-r1", content_id="quiz-r1")
    db.add_all([doc, quiz])
    await db.commit()

    # q1 right (idx 0), q2 wrong (idx 0, answer is 1).
    resp = await client.post(
        "/api/quiz/quiz-r1/attempt", json={"answers": {"q1": 0, "q2": 0}}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["score"] == 0.5
    assert body["correct_count"] == 1
    assert body["total_count"] == 2

    # Reaction 1: concept mastery + FSRS (q1 correct, q2 wrong).
    mastery = await memory_store.get_concept_mastery(db)
    assert mastery["Photosynthesis"]["correct"] == 1
    assert mastery["Photosynthesis"]["seen"] == 1
    assert mastery["Calvin cycle"]["wrong"] == 1
    assert mastery["Calvin cycle"]["fsrs"] is not None  # FSRS scheduled

    # Reaction 2: learner profile.
    profile = await memory_store.get_learner_profile(db)
    assert profile["stats"]["total_quizzes"] == 1
    assert profile["stats"]["score_history"][0]["score"] == 0.5

    # Reaction 3: recommendation session tracking.
    session_data = await memory_store.read_memory(db, "user", "", "session")
    assert session_data["actions"][-1]["tool"] == "quiz"
    assert session_data["actions"][-1]["doc_id"] == "doc-r1"

    # Reaction 4: weak topics — gated OFF by default (proactive disabled).
    weak = await memory_store.get_weak_topics(db)
    assert weak == []

    # Every reaction is logged in the event log.
    rows = await _handler_rows("QuizAttempted")
    assert set(rows) == {
        None,
        "study.update_mastery",
        "study.update_profile_from_quiz",
        "study.record_activity",
        "study.detect_weak_topics",
    }
    assert all(r.status == "ok" for r in rows.values())
    assert rows[None].payload["score"] == 0.5


async def test_weak_topics_recorded_only_when_struggling(client, db, monkeypatch):
    monkeypatch.setattr(settings, "proactive_enabled", True)

    doc, quiz = make_quiz(doc_id="doc-r2", content_id="quiz-r2")
    db.add_all([doc, quiz])
    # Weak-topic matching needs the doc's analysis (concept list) in memory.
    await memory_store.write_memory(
        db, "doc", "doc-r2", "analysis",
        {"concepts": ["Photosynthesis", "Calvin cycle"], "difficulty": "medium"},
    )
    await db.commit()

    # Ace it (1.0) — no weak topics, despite the low threshold gate passing
    # its other conditions.
    resp = await client.post(
        "/api/quiz/quiz-r2/attempt", json={"answers": {"q1": 0, "q2": 1}}
    )
    assert resp.json()["score"] == 1.0
    assert await memory_store.get_weak_topics(db) == []
    attempts = await memory_store.read_memory(db, "doc", "doc-r2", "quiz_attempts")
    assert attempts is None

    # Fail it (0.0) — both concepts matched from the missed prompts.
    resp = await client.post(
        "/api/quiz/quiz-r2/attempt", json={"answers": {"q1": 1, "q2": 0}}
    )
    assert resp.json()["score"] == 0.0
    weak = await memory_store.get_weak_topics(db)
    names = {w["topic"] for w in weak}
    assert names == {"Photosynthesis", "Calvin cycle"}
    attempts = await memory_store.read_memory(db, "doc", "doc-r2", "quiz_attempts")
    assert attempts == 1


async def test_flashcard_review_reactions(client, db):
    doc = Document(
        id="doc-r3",
        filename="cards.pdf",
        mime="application/pdf",
        file_path="/tmp/cards.pdf",
        text="…",
        kind="text",
    )
    deck = ContentItem(
        id="deck-r3",
        document_id="doc-r3",
        type="flashcards",
        content={
            "title": "Deck",
            "cards": [
                {"id": "c1", "front": "F", "back": "B", "concept": "Mitosis"},
                {"id": "c2", "front": "F", "back": "B", "concept": "Meiosis"},
            ],
        },
    )
    db.add_all([doc, deck])
    await db.commit()

    # One result without a concept — recovered from the stored card.
    resp = await client.post(
        "/api/flashcards/deck-r3/review",
        json={
            "results": [
                {"card_id": "c1", "concept": "Mitosis", "known": True},
                {"card_id": "c2", "concept": "", "known": False},
            ]
        },
    )
    assert resp.status_code == 201
    assert resp.json()["recorded"] == 2

    mastery = await memory_store.get_concept_mastery(db)
    assert mastery["Mitosis"]["correct"] == 1
    assert mastery["Meiosis"]["wrong"] == 1  # recovered from the card

    profile = await memory_store.get_learner_profile(db)
    assert profile["stats"]["total_flashcard_reviews"] == 2

    session_data = await memory_store.read_memory(db, "user", "", "session")
    assert session_data["actions"][-1]["tool"] == "flashcards"

    rows = await _handler_rows("FlashcardsReviewed")
    assert set(rows) == {
        None,
        "study.update_mastery",
        "study.update_profile_from_cards",
        "study.record_activity",
    }
    assert all(r.status == "ok" for r in rows.values())


async def test_study_session_review_reactions(client, db):
    # Snapshot pre-state — other tests share the session DB.
    before_profile = await memory_store.get_learner_profile(db)
    before_reviews = before_profile["stats"]["total_flashcard_reviews"] or 0

    resp = await client.post(
        "/api/study-session/sess-1/review",
        json={
            "results": [
                {"card_id": "c1", "known": True, "concept": "Osmosis", "content_id": None},
                {"card_id": "c2", "known": False, "concept": "", "content_id": None},
            ]
        },
    )
    assert resp.status_code == 200
    assert resp.json()["recorded"] == 1  # blank concept skipped

    mastery = await memory_store.get_concept_mastery(db)
    assert mastery["Osmosis"]["seen"] == 1

    profile = await memory_store.get_learner_profile(db)
    assert profile["stats"]["total_flashcard_reviews"] == before_reviews + 1

    session_data = await memory_store.read_memory(db, "user", "", "session")
    assert session_data["actions"][-1]["tool"] == "flashcards"

    rows = await _handler_rows("StudySessionReviewed")
    assert "study.update_mastery" in rows
    assert rows["study.update_mastery"].status == "ok"


async def test_events_endpoint_lists_log(client, db):
    doc, quiz = make_quiz(doc_id="doc-r4", content_id="quiz-r4")
    db.add_all([doc, quiz])
    await db.commit()
    await client.post(
        "/api/quiz/quiz-r4/attempt", json={"answers": {"q1": 0, "q2": 1}}
    )

    resp = await client.get("/api/events?limit=500")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 5
    # The 5 newest QuizAttempted rows (dispatch + 4 handlers) are all ok.
    quiz_rows = [r for r in rows if r["event_type"] == "QuizAttempted"]
    assert len(quiz_rows) >= 5
    assert all(r["status"] == "ok" for r in quiz_rows[:5])
    assert {r["handler"] for r in quiz_rows[:5]} == {
        None,
        "study.update_mastery",
        "study.update_profile_from_quiz",
        "study.record_activity",
        "study.detect_weak_topics",
    }

    # Filter by event_type works.
    resp = await client.get("/api/events?event_type=QuizAttempted")
    assert all(r["event_type"] == "QuizAttempted" for r in resp.json())
