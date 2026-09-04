"""Session-composer suite — property tests over the real composition route
(deterministic, no LLM): the desirable-difficulty mix ratios, most-forgotten-
first ordering, pool-shortage fallbacks, and scope filtering.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.agent.memory import write_memory
from app.db import SessionLocal, init_db
from app.models import ContentItem, Document
from app.routes.study_session import StudySessionRequest, compose_session

from evals.report import record

pytestmark = pytest.mark.evals

NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
async def _clean_state():
    """Each property test seeds its own world — wipe the shared throwaway DB."""
    from sqlalchemy import delete

    from app.models import AgentMemory

    await init_db()
    async with SessionLocal() as s:
        await s.execute(delete(ContentItem))
        await s.execute(delete(Document))
        await s.execute(delete(AgentMemory))
        await s.commit()
    yield


async def _seed_decks(session, doc_id_prefix: str, concepts: list[str]) -> str:
    """One document + a flashcard deck covering `concepts`."""
    doc = Document(
        id=f"{doc_id_prefix}-doc",
        filename=f"{doc_id_prefix}.pdf",
        mime="application/pdf",
        file_path="/tmp/x.pdf",
        text="synthetic",
        kind="text",
    )
    deck = ContentItem(
        id=f"{doc_id_prefix}-deck",
        document_id=doc.id,
        type="flashcards",
        content={
            "cards": [
                {
                    "id": f"{doc_id_prefix}-c{i}",
                    "concept": c,
                    "front": f"front {c}?",
                    "back": f"back {c}",
                }
                for i, c in enumerate(concepts)
            ]
        },
    )
    session.add_all([doc, deck])
    await session.commit()
    return doc.id


def _mastery_entry(days_ago: float, stability: float, correct: int, seen: int) -> dict:
    """A concept_mastery entry whose FSRS state is due now, with controlled
    retrievability R (power law, strictly decreasing in days_ago)."""
    from app.agent.fsrs_scheduler import schedule_review

    state = schedule_review(None, 3)
    state["stability"] = stability
    last = NOW - timedelta(days=days_ago)
    state["last_review"] = last.isoformat()
    state["due"] = (last + timedelta(hours=1)).isoformat()  # long past
    return {
        "correct": correct,
        "wrong": seen - correct,
        "seen": seen,
        "mastery_pct": round(correct / seen, 3),
        "fsrs": state,
    }


async def _seed_profile(score_history: list[float]) -> None:
    async with SessionLocal() as s:
        await write_memory(
            s, "user", "", "learner_profile",
            {"learner_level": "intermediate",
             "stats": {"score_history": [{"score": x} for x in score_history]}},
        )
        await s.commit()


async def _compose(count: int = 20):
    async with SessionLocal() as s:
        return await compose_session(StudySessionRequest(count=count), s)


async def test_mix_ratio_by_accuracy_band():
    """Struggling → review-heavy; mastering → new-heavy; sweet spot between."""
    await init_db()
    due = [f"due{i}" for i in range(20)]
    new = [f"new{i}" for i in range(20)]
    async with SessionLocal() as s:
        doc_id = await _seed_decks(s, "mix", due + new)
        mastery = {
            c: _mastery_entry(days_ago=1 + i, stability=5.0, correct=3, seen=4)
            for i, c in enumerate(due)
        }
        await write_memory(s, "user", "", "concept_mastery", mastery)
        await write_memory(s, "user", "", "learner_profile", {"stats": {}})
        await s.commit()

    expected = {
        "struggling": (0.50, 16, 4),   # avg < 0.70 → 80% review
        "sweet": (0.75, 12, 8),        # → 60% review
        "mastering": (0.90, 8, 12),    # avg > 0.85 → 40% review
    }
    for band, (score, want_review, want_new) in expected.items():
        await _seed_profile([score] * 5)
        resp = await _compose()
        got = (resp.mix["review"], resp.mix["new"])
        ok = got == (want_review, want_new)
        record(
            "session", f"mix_{band}", case="synthetic-20-20",
            score=1.0 if ok else 0.0, threshold=1.0, success=ok,
            reason=f"expected ({want_review},{want_new}) got {got}",
        )
        assert ok, f"{band}: expected mix ({want_review},{want_new}), got {got}"


async def test_most_forgotten_first():
    """Review slots must fill highest-failure-risk first.

    Equal correct-rates across concepts, so the blended risk ordering (see
    fsrs_scheduler.failure_risk) reduces to the forgetting curve: strictly
    decreasing retrievability in elapsed days = most elapsed first.
    """
    await init_db()
    # Elapsed days 1..20 with equal stability → R strictly decreasing.
    due = [f"forget{i}" for i in range(20)]
    new = [f"fresh{i}" for i in range(5)]
    async with SessionLocal() as s:
        await _seed_decks(s, "order", due + new)
        mastery = {
            c: _mastery_entry(days_ago=1 + i, stability=10.0, correct=2, seen=3)
            for i, c in enumerate(due)
        }
        await write_memory(s, "user", "", "concept_mastery", mastery)
        await write_memory(s, "user", "", "learner_profile",
                           {"stats": {"score_history": [{"score": 0.5}] * 5}})
        await s.commit()

    resp = await _compose()
    review_cards = [c for c in resp.cards if c.source == "review"]
    # Most-forgotten-first = retrievability ASCENDING = most elapsed first.
    expected_order = list(reversed(due))[: len(review_cards)]
    order_ok = [c.concept for c in review_cards] == expected_order
    record(
        "session", "most_forgotten_first", case="synthetic",
        score=1.0 if order_ok else 0.0, threshold=1.0, success=order_ok,
        reason=f"review order: {[c.concept for c in review_cards][:6]}…",
    )
    assert order_ok, "review cards are not most-forgotten-first"


async def test_scope_filtering():
    """document scope must only draw from that document's deck."""
    await init_db()
    async with SessionLocal() as s:
        await _seed_decks(s, "scopeA", [f"a{i}" for i in range(10)])
        await _seed_decks(s, "scopeB", [f"b{i}" for i in range(10)])
        await write_memory(s, "user", "", "concept_mastery", {})
        await write_memory(s, "user", "", "learner_profile", {"stats": {}})
        await s.commit()

    async with SessionLocal() as s:
        resp = await compose_session(
            StudySessionRequest(
                count=10, scope="document", document_id="scopeA-doc"
            ),
            s,
        )
    ok = all(c.concept.startswith("a") for c in resp.cards) and len(resp.cards) == 10
    record(
        "session", "scope_filter", case="synthetic",
        score=1.0 if ok else 0.0, threshold=1.0, success=ok,
        reason=f"{len(resp.cards)} cards, all from scoped doc: {ok}",
    )
    assert ok, "document scope leaked cards from another document"


async def test_pool_shortage_fallback():
    """When one pool runs dry, slots shift to the other — never empty output."""
    await init_db()
    async with SessionLocal() as s:
        await _seed_decks(s, "short", [f"s{i}" for i in range(3)])  # tiny deck
        await write_memory(s, "user", "", "concept_mastery", {})
        await write_memory(s, "user", "", "learner_profile",
                           {"stats": {"score_history": [{"score": 0.9}] * 5}})
        await s.commit()

    resp = await _compose(count=20)
    ok = len(resp.cards) == 3 and resp.mix["new"] == 3 and resp.mix["review"] == 0
    record(
        "session", "pool_shortage_fallback", case="synthetic",
        score=1.0 if ok else 0.0, threshold=1.0, success=ok,
        reason=f"requested 20 from 3-concept deck → {resp.mix}",
    )
    assert ok, f"pool shortage mishandled: mix={resp.mix}"
