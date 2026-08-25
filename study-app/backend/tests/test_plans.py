"""Study plan tests — module-scoped sessions, planner validation +
carry-over, staleness/cooldown, and progress auto-detection. The LLM call
is monkeypatched; everything else runs for real through the bus.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.agent import planner as planner_store
from app.db import SessionLocal
from app.events import bus
from app.events.domain import (
    DocumentAnalyzed,
    QuizAttempted,
    QuestionOutcome,
    StudySessionReviewed,
    CardOutcome,
)
from app.models import Document, Lesson, Module, StudyPlan, UserActivity


async def _seed_module_with_docs(db, mid: str) -> tuple[Module, list[Document]]:
    module = Module(
        id=mid,
        title="Cell Biology",
        exam_date=date.today() + timedelta(days=10),
    )
    lesson = Lesson(id=f"les-{mid}", module_id=mid, title="Week 1")
    docs = [
        Document(
            id=f"{mid}-doc1",
            filename="Photosynthesis Lecture.pdf",
            mime="application/pdf",
            file_path="/tmp/p1.pdf",
            text="photosynthesis…",
            kind="text",
            lesson_id=f"les-{mid}",
        ),
        Document(
            id=f"{mid}-doc2",
            filename="Textbook.pdf",
            mime="application/pdf",
            file_path="/tmp/p2.pdf",
            text="…",
            kind="text",
            module_id=mid,
        ),
        Document(  # NOT in the module — must be filtered out of module scopes
            id=f"{mid}-out",
            filename="Outside.pdf",
            mime="application/pdf",
            file_path="/tmp/p3.pdf",
            text="…",
            kind="text",
        ),
    ]
    db.add_all([module, lesson, *docs])
    await db.commit()
    return module, docs


def _fake_llm_items(result: dict):
    from app.agent import planner

    async def fake(messages, **kwargs):
        return result

    return fake


async def test_module_scoped_study_session(client, db):
    """scope=module composes only from the module's decks (lesson docs +
    direct module docs), never from outside documents."""
    MID = "mod-scope"
    await _seed_module_with_docs(db, MID)
    from app.models import ContentItem

    db.add_all([
        ContentItem(
            id=f"d{MID}-1", document_id=f"{MID}-doc1", type="flashcards",
            content={"cards": [
                {"id": "c1", "front": "F", "back": "B", "concept": "Photosynthesis"},
                {"id": "c2", "front": "F", "back": "B", "concept": "Chlorophyll"},
            ]},
        ),
        ContentItem(
            id=f"d{MID}-2", document_id=f"{MID}-doc2", type="flashcards",
            content={"cards": [
                {"id": "c3", "front": "F", "back": "B", "concept": "Mitosis"},
            ]},
        ),
        ContentItem(
            id=f"d{MID}-o", document_id=f"{MID}-out", type="flashcards",
            content={"cards": [
                {"id": "c4", "front": "F", "back": "B", "concept": "Irrelevant"},
            ]},
        ),
    ])
    await db.commit()

    resp = await client.post(
        "/api/study-session",
        json={"type": "flashcards", "count": 20, "scope": "module", "module_id": MID},
    )
    assert resp.status_code == 201
    concepts = {c["concept"] for c in resp.json()["cards"]}
    assert concepts == {"Photosynthesis", "Chlorophyll", "Mitosis"}  # no "Irrelevant"


async def test_planner_validates_and_carries_over(client, db, monkeypatch):
    MID = "mod-val"
    module, docs = await _seed_module_with_docs(db, MID)

    # First generation: one real doc item, one hallucinated doc item.
    monkeypatch.setattr(
        planner_store,
        "chat_json",
        _fake_llm_items({
            "items": [
                {
                    "type": "take_quiz",
                    "title": "Photosynthesis check",
                    "rationale": "due: 4 concepts",
                    "day_offset": 0,
                    "estimate_mins": 15,
                    "document_title": "Photosynthesis Lecture.pdf",
                    "concepts": ["Photosynthesis"],
                },
                {
                    "type": "read_document",
                    "title": "Read the made-up doc",
                    "rationale": "hallucination",
                    "day_offset": 1,
                    "estimate_mins": 10,
                    "document_title": "Doc That Does Not Exist.pdf",
                    "concepts": None,
                },
            ]
        }),
    )
    plan = await planner_store.generate_study_plan(db, module.id)
    assert plan is not None
    assert plan.version == 1
    assert len(plan.items) == 1  # hallucinated doc dropped
    assert plan.items[0]["target"]["document_id"] == f"{MID}-doc1"
    assert plan.meta["days_to_exam"] == 10

    # Mark it done, then regenerate — progress must survive.
    from sqlalchemy.orm import attributes

    plan.items[0]["status"] = "done"
    plan.items[0]["done_kind"] = "auto"
    attributes.flag_modified(plan, "items")
    await db.commit()

    plan2 = await planner_store.generate_study_plan(db, module.id)
    assert plan2.version == 2
    assert plan2.items[0]["status"] == "done"  # carried over
    assert plan2.stale_reasons == []


async def test_staleness_marks_and_cooldown(client, db, monkeypatch):
    MID = "mod-stale"
    module, docs = await _seed_module_with_docs(db, MID)
    monkeypatch.setattr(
        planner_store, "chat_json", _fake_llm_items({"items": []})
    )
    plan = await planner_store.generate_study_plan(db, module.id)
    assert plan is not None

    # New content analyzed in the module → plan marked stale + event published.
    regen_calls = []
    monkeypatch.setattr(
        planner_store,
        "generate_study_plan",
        _counting_wrapper(planner_store.generate_study_plan, regen_calls),
    )
    await bus.publish(DocumentAnalyzed(document_id=f"{MID}-doc1", analysis={"concepts": []}))
    await bus.drain()

    async with SessionLocal() as s:
        fresh = (await s.execute(
            select(StudyPlan).where(StudyPlan.module_id == module.id)
        )).scalars().first()
    assert "new content analyzed" in (fresh.stale_reasons or [])

    # Cooldown: plan was generated <1s ago → the regen handler must have
    # skipped the actual regeneration despite the stale flag.
    assert len(regen_calls) == 0
    assert fresh.version == 1


def _counting_wrapper(fn, calls):
    async def wrapper(session, module_id):
        calls.append(module_id)
        return await fn(session, module_id)

    return wrapper


async def test_quiz_auto_completes_plan_item(client, db, monkeypatch):
    MID = "mod-quiz"
    module, docs = await _seed_module_with_docs(db, MID)
    from app.models import ContentItem

    quiz = ContentItem(
        id="quiz-p1", document_id=f"{MID}-doc1", type="quiz",
        content={"questions": [
            {"id": "q1", "prompt": "What is PlanConceptA?", "options": ["a", "b", "c", "d"],
             "answer_idx": 0, "concept": "PlanConceptA"},
        ]},
    )
    db.add(quiz)
    monkeypatch.setattr(
        planner_store, "chat_json",
        _fake_llm_items({
            "items": [{
                "type": "take_quiz",
                "title": "Photosynthesis check",
                "rationale": "weak concepts",
                "day_offset": 0,
                "estimate_mins": 15,
                "document_title": "Photosynthesis Lecture.pdf",
                "concepts": ["PlanConceptA"],
            }]
        }),
    )
    await db.commit()
    plan = await planner_store.generate_study_plan(db, module.id)
    assert plan.items[0]["status"] == "pending"

    # Take the quiz through the API — the bus should auto-complete the item.
    resp = await client.post(
        "/api/quiz/quiz-p1/attempt", json={"answers": {"q1": 0}}
    )
    assert resp.status_code == 201
    await bus.drain()

    async with SessionLocal() as s:
        fresh = (await s.execute(
            select(StudyPlan).where(StudyPlan.module_id == module.id)
        )).scalars().first()
    item = fresh.items[0]
    assert item["status"] == "done"
    assert item["done_kind"] == "auto"
    assert "1/1" in item["done_reason"]
    # Quiz results also flagged the plan stale.
    assert "quiz results" in (fresh.stale_reasons or [])


async def test_session_auto_completes_review_item(client, db, monkeypatch):
    MID = "mod-sess"
    module, docs = await _seed_module_with_docs(db, MID)
    monkeypatch.setattr(
        planner_store, "chat_json",
        _fake_llm_items({
            "items": [{
                "type": "review_concepts",
                "title": "Review due concepts",
                "rationale": "3 due",
                "day_offset": 0,
                "estimate_mins": 20,
                "document_title": None,
                "concepts": ["PlanReviewA", "PlanReviewB"],
            }]
        }),
    )
    plan = await planner_store.generate_study_plan(db, module.id)
    assert plan.items[0]["type"] == "review_concepts"

    # Complete a study session covering both concepts.
    await bus.publish(
        StudySessionReviewed(
            session_id="s1",
            results=[
                CardOutcome(card_id="c1", concept="PlanReviewA", known=True),
                CardOutcome(card_id="c2", concept="PlanReviewB", known=False),
            ],
        )
    )
    await bus.drain()

    async with SessionLocal() as s:
        fresh = (await s.execute(
            select(StudyPlan).where(StudyPlan.module_id == module.id)
        )).scalars().first()
    assert fresh.items[0]["status"] == "done"
    assert "2/2" in fresh.items[0]["done_reason"]


async def test_plan_endpoints(client, db, monkeypatch):
    MID = "mod-api"
    module, docs = await _seed_module_with_docs(db, MID)

    # No plan yet → 404-shaped null.
    resp = await client.get(f"/api/modules/{MID}/plan")
    assert resp.status_code == 404

    # Generate through the API (with exam date set in the same call).
    monkeypatch.setattr(
        planner_store, "chat_json",
        _fake_llm_items({
            "items": [{
                "type": "read_document",
                "title": "Read the textbook",
                "rationale": "unread",
                "day_offset": 1,
                "estimate_mins": 30,
                "document_title": "Textbook.pdf",
                "concepts": None,
            }]
        }),
    )
    resp = await client.post(
        f"/api/modules/{MID}/plan",
        json={"exam_date": "2026-12-01"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["staleness"] == {"stale": False, "reasons": []}
    assert body["items"][0]["target"]["document_id"] == f"{MID}-doc2"

    # The module now carries the exam date.
    db.expire_all()
    fresh_module = await db.get(Module, MID)
    assert str(fresh_module.exam_date) == "2026-12-01"

    # Manual toggle.
    item_id = body["items"][0]["id"]
    resp = await client.patch(
        f"/api/plans/{body['id']}/items/{item_id}",
        json={"status": "done"},
    )
    assert resp.status_code == 200
    assert resp.json()["item"]["done_kind"] == "manual"
