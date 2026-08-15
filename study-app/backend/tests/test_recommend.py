"""Recommendation-engine tests — the behavioral-understanding wiring.

The strategies now consume the learner model's measurements (slow recall,
dwell, habitual hour, session outcomes). These tests seed memory keys
directly and assert strategy/engine behavior — no LLMs anywhere.

Shared-DB discipline (see test_study_reactions): unique concept/doc names
per test, membership assertions instead of exact lists.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.agent import behavior as behavior_store
from app.agent import memory as memory_store
from app.recommend.context import UserContext, build_context
from app.recommend.engine import PEAK_HOUR_BOOST, engine as rec_engine
from app.recommend.strategies import (
    DueReviewReadyStrategy,
    FlashcardStrategy,
    QuizGapStrategy,
    QuizStrategy,
    WeakSpotStrategy,
)
from app.models import Document

from conftest import make_quiz


def _doc(doc_id: str, filename: str) -> SimpleNamespace:
    return SimpleNamespace(id=doc_id, filename=filename)


async def _seed_behavior_keys(db, slow_concept: str) -> None:
    """Seed the learner-model keys build_context reads (unique names)."""
    now_hour = datetime.now(timezone.utc).hour
    await memory_store.write_memory(
        db, "user", "", behavior_store.PATTERNS_KEY,
        {"best_study_hour": now_hour, "avg_quiz_duration_secs": 90,
         "sessions": {"completed": 2, "abandoned": 0}},
    )
    await memory_store.write_memory(
        db, "user", "", behavior_store.ENGAGEMENT_KEY,
        {"docs": {"doc-rec-seen": {"views": 3, "dwell_secs": 120.0}},
         "total_dwell_secs": 120.0, "actions_count": 10},
    )
    await memory_store.write_memory(
        db, "user", "", "learner_insights",
        {"summary": "Test summary.", "traits": [], "habits": "",
         "updated_at": datetime.now(timezone.utc).isoformat(),
         "activities_seen": 30},
    )
    # Slow concept with a FUTURE due date so FSRS strategies stay quiet.
    await memory_store.write_memory(
        db, "user", "", "concept_mastery",
        {slow_concept: {
            "mastery_pct": 0.2,
            "latency": {"avg_secs": 34.0, "samples": 4},
            "fsrs": {"due": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
                     "stability": 4.0},
        }},
    )


async def test_build_context_reads_behavioral_keys(db):
    await _seed_behavior_keys(db, "RecSlowConceptX")
    seen_doc, _ = make_quiz(doc_id="doc-rec-seen", content_id="quiz-rec-seen")
    unseen_doc = Document(
        id="doc-rec-unseen", filename="never-opened.pdf",
        mime="application/pdf", file_path="/tmp/x.pdf",
        text="unread", kind="text",
    )
    db.add_all([seen_doc, unseen_doc])
    await memory_store.write_memory(
        db, "doc", "doc-rec-seen", "analysis", {"concepts": ["RecSlowConceptX"]},
    )
    await memory_store.write_memory(
        db, "doc", "doc-rec-unseen", "analysis", {"concepts": ["OtherY"]},
    )
    await db.commit()

    ctx = await build_context(db)

    assert ctx.insights.get("summary") == "Test summary."
    assert isinstance(ctx.patterns.get("best_study_hour"), int)
    assert "doc-rec-seen" in (ctx.engagement.get("docs") or {})
    slow_names = {c["concept"] for c in ctx.slow_concepts}
    assert "RecSlowConceptX" in slow_names
    assert "doc-rec-unseen" in ctx.neglected_docs
    assert "doc-rec-seen" not in ctx.neglected_docs
    assert ctx.is_peak_hour is True  # best_study_hour seeded to now


def test_weak_spot_fires_and_cites_latency():
    ctx = UserContext(
        slow_concepts=[
            {"concept": "ChlorophyllW", "avg_secs": 34.0, "samples": 4, "mastery_pct": 0.2},
            {"concept": "ATPW", "avg_secs": 21.0, "samples": 3, "mastery_pct": 0.3},
        ],
        analyses={"doc-w": {"concepts": ["ChlorophyllW", "ATPW"]}},
        documents={"doc-w": _doc("doc-w", "bio.pdf")},
    )
    result = WeakSpotStrategy().evaluate(ctx)
    assert result is not None
    assert result.document_id == "doc-w"
    assert result.score == 0.65  # 0.55 + 0.05 * 2
    assert "ChlorophyllW" in result.rationale and "ATPW" in result.rationale
    assert "~34" in result.rationale


def test_weak_spot_guards():
    strat = WeakSpotStrategy()
    slow = [{"concept": "C1W", "avg_secs": 30.0, "samples": 2, "mastery_pct": 0.1}]

    # Defers to FSRS when actual reviews are due.
    ctx = UserContext(
        slow_concepts=slow,
        due_cards=[{"id": "c", "front": "f", "back": "b", "concept": "C1W", "document_id": "d"}],
    )
    assert strat.evaluate(ctx) is None

    # Nothing weak → None.
    assert strat.evaluate(UserContext()) is None

    # One slow concept alone → None; corroborated by weak topics → fires.
    ctx = UserContext(
        slow_concepts=slow,
        analyses={"doc-w": {"concepts": ["C1W"]}},
        documents={"doc-w": _doc("doc-w", "bio.pdf")},
    )
    assert strat.evaluate(ctx) is None
    ctx.weak_topics = [{"topic": "C1W", "missed_count": 3, "last_seen": ""}]
    result = strat.evaluate(ctx)
    assert result is not None and round(result.score, 6) == 0.60


def test_due_review_ready_annotates_slow_concepts():
    ctx = UserContext(
        due_concepts=[{"concept": "SlowDueW", "due_in_days": -1.0}],
        due_cards=[{"id": "c1", "front": "f", "back": "b", "concept": "SlowDueW", "document_id": "doc-d"}],
        slow_concepts=[{"concept": "SlowDueW", "avg_secs": 28.0, "samples": 3, "mastery_pct": 0.3}],
        documents={"doc-d": _doc("doc-d", "notes.pdf")},
    )
    result = DueReviewReadyStrategy().evaluate(ctx)
    assert result is not None
    assert "recall slowly" in result.rationale and "~28" in result.rationale


def test_quiz_gap_prefers_neglected_doc():
    contents = {"notes": [object()], "quiz": [], "flashcards": []}
    ctx = UserContext(
        content_by_doc={"doc-seen-w": contents, "doc-unseen-w": contents},
        documents={
            "doc-seen-w": _doc("doc-seen-w", "opened.pdf"),
            "doc-unseen-w": _doc("doc-unseen-w", "never-opened.pdf"),
        },
        neglected_docs=["doc-unseen-w"],
    )
    result = QuizGapStrategy().evaluate(ctx)
    assert result is not None
    assert result.document_id == "doc-unseen-w"
    assert "haven't opened" in result.rationale


def test_format_tilt_favors_flashcards():
    contents = {"notes": [object()], "quiz": [], "flashcards": []}
    ctx = UserContext(
        content_by_doc={"doc-f": contents},
        documents={"doc-f": _doc("doc-f", "notes.pdf")},
        patterns={"avg_quiz_duration_secs": 150, "sessions": {"completed": 1, "abandoned": 0}},
    )
    flash = FlashcardStrategy().evaluate(ctx)
    quiz = QuizStrategy().evaluate(ctx)
    assert flash is not None and quiz is not None
    assert flash.score == 0.35  # 0.25 + 0.10 tilt
    assert quiz.score == 0.25   # 0.30 - 0.05 tilt
    assert "running long" in flash.rationale


def test_engine_peak_hour_boost(monkeypatch):
    import sys

    engine_mod = sys.modules["app.recommend.engine"]
    monkeypatch.setattr(engine_mod.random, "random", lambda: 0.99)

    def decide(peak: bool) -> float:
        ctx = UserContext(
            documents={"d": _doc("d", "x.pdf")},
            is_peak_hour=peak,
            enabled_features={"fallback"},  # isolate one practice strategy
        )
        out = rec_engine.decide(ctx)
        assert out["primary"] is not None
        return out["primary"]["score"]

    off, on = decide(False), decide(True)
    assert round(on - off, 6) == round(PEAK_HOUR_BOOST, 6)


async def test_recommend_endpoint_smoke(client):
    resp = await client.get("/api/recommend")
    assert resp.status_code == 200
    body = resp.json()
    assert body["impression_id"]
    assert "context" in body and "alternatives" in body
    if body["primary"] is not None:
        assert body["primary"]["rationale"]  # enriched or not, always present
