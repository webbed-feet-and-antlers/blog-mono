"""Study session composer — composes the optimal mix of review + new cards.

When the student wants to study, this endpoint assembles a session that:
  1. SURFACES due concepts (FSRS forgetting curve) as review cards
  2. INTRODUCES new concepts (untested) as new cards
  3. BALANCES the mix for desirable difficulty (70-85% target accuracy)
  4. VARIES card wording to prevent pattern-matching (recognition vs recall)

The session is composed instantly from pre-generated decks — no LLM call
needed at session time (cards are auto-generated on upload).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, user_ref_id
from ..agent import memory as memory_store
from ..agent import fsrs_scheduler
from ..db import get_session
from ..events import bus
from ..events.domain import CardOutcome, StudySessionReviewed
from ..models import ContentItem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/study-session", tags=["study-session"])


# --- Request/Response schemas ---


class StudySessionRequest(BaseModel):
    type: str = "flashcards"  # "flashcards" | "quiz" (quiz deferred)
    count: int = 20
    scope: str = "global"  # "global" | "document" | "module"
    document_id: str | None = None
    module_id: str | None = None


class SessionCard(BaseModel):
    id: str
    front: str
    back: str
    concept: str
    source: str  # "review" | "new"
    content_id: str | None = None  # source deck for review submission


class SessionReviewItem(BaseModel):
    card_id: str
    known: bool
    concept: str
    content_id: str | None = None
    # Seconds spent on the card before the decision (optional).
    secs: float | None = None


class SessionReviewRequest(BaseModel):
    results: list[SessionReviewItem] = []
    duration_secs: float | None = None


class StudySessionResponse(BaseModel):
    id: str
    type: str
    cards: list[SessionCard]
    mix: dict  # {"review": int, "new": int}
    rationale: str


# --- Session composition ---


@router.post("", response_model=StudySessionResponse, status_code=201)
async def compose_session(
    req: StudySessionRequest, session: AsyncSession = Depends(get_session)
):
    """Compose the optimal study session: mix of review (FSRS-due) + new cards.

    Instant — assembles from pre-generated decks. No LLM call.
    """
    session_id = uuid.uuid4().hex[:16]
    target_count = req.count

    # 1. Load all flashcard cards across all documents (or scoped to one doc
    #    or one module — the module's doc set includes lesson docs).
    stmt = select(ContentItem).where(
        ContentItem.type == "flashcards",
        ContentItem.user_id == user_ref_id(),
    )
    if req.scope == "document" and req.document_id:
        stmt = stmt.where(ContentItem.document_id == req.document_id)
    elif req.scope == "module" and req.module_id:
        from ..agent.planner import get_module_doc_ids

        module_doc_ids = await get_module_doc_ids(session, req.module_id)
        if module_doc_ids:
            stmt = stmt.where(ContentItem.document_id.in_(module_doc_ids))
        else:
            # Empty module → no decks (document_id is never null in practice).
            stmt = stmt.where(ContentItem.document_id.is_(None))
    result = await session.execute(stmt)
    all_decks = result.scalars().all()

    # Build a concept → cards index from all decks.
    concept_cards: dict[str, list[dict]] = {}
    for deck in all_decks:
        for card in deck.content.get("cards", []):
            concept = (card.get("concept") or "").strip()
            if concept:
                concept_cards.setdefault(concept, []).append({
                    "id": card["id"],
                    "front": card.get("front", ""),
                    "back": card.get("back", ""),
                    "concept": concept,
                    "content_id": deck.id,
                })

    # 2. Load FSRS state for all concepts.
    mastery = await memory_store.get_concept_mastery(session)

    # 3. Classify concepts: due (review) vs untested (new) vs mastered (skip).
    now = datetime.now(timezone.utc)
    due_concepts: list[tuple[str, float, bool]] = []  # (concept, risk, active)
    new_concepts: list[str] = []
    for concept, cards in concept_cards.items():
        entry = mastery.get(concept, {})
        fsrs = entry.get("fsrs")
        seen = entry.get("seen", 0)
        if seen == 0 or not fsrs:
            new_concepts.append(concept)
        elif fsrs_scheduler.is_due(fsrs, now):
            risk = fsrs_scheduler.failure_risk(entry, fsrs, now)
            active = fsrs_scheduler.is_recently_active(
                entry.get("last_attempt_ts"), now
            )
            due_concepts.append((concept, risk, active))
        # else: not due — skip (review later at the optimal time)

    # Review slots fill recently-active concepts first (the learner's
    # current orbit), then by failure risk within each tier — matching the
    # recommender's due-deck ordering (see fsrs_scheduler.failure_risk and
    # is_recently_active).
    due_concepts.sort(key=lambda x: (x[2], x[1]), reverse=True)

    # 4. Compute mix ratio from recent accuracy (desirable difficulty).
    profile = await memory_store.get_learner_profile(session)
    score_history = (profile.get("stats") or {}).get("score_history") or []
    recent_scores = [h["score"] for h in score_history[-5:]] if score_history else []
    recent_accuracy = sum(recent_scores) / len(recent_scores) if recent_scores else 0.75

    if recent_accuracy < 0.70:
        review_ratio = 0.80  # struggling → more reinforcement
    elif recent_accuracy > 0.85:
        review_ratio = 0.40  # mastering → push new material
    else:
        review_ratio = 0.60  # sweet spot

    review_count = round(target_count * review_ratio)
    new_count = target_count - review_count

    # Adjust if not enough due/new concepts available.
    available_review = len(due_concepts)
    available_new = len(new_concepts)
    if available_review < review_count:
        # Not enough due concepts → shift slots to new.
        new_count += review_count - available_review
        review_count = available_review
    if available_new < new_count:
        review_count += new_count - available_new
        new_count = available_new
    # Clamp to what's actually available.
    review_count = min(review_count, available_review)
    new_count = min(new_count, available_new)

    # 5. Assemble cards.
    composed_cards: list[SessionCard] = []

    # Review slots: active-orbit, highest-risk concepts first, one per concept.
    for i, (concept, _, _) in enumerate(due_concepts[:review_count]):
        cards = concept_cards.get(concept, [])
        if not cards:
            continue
        # Pick the first available card for this concept.
        # (Variant rotation would pick the least-recently-seen variant here.)
        card = cards[0]
        composed_cards.append(SessionCard(
            id=card["id"],
            front=card["front"],
            back=card["back"],
            concept=concept,
            source="review",
            content_id=card.get("content_id"),
        ))

    # New slots: untested concepts.
    for concept in new_concepts[:new_count]:
        cards = concept_cards.get(concept, [])
        if not cards:
            continue
        card = cards[0]
        composed_cards.append(SessionCard(
            id=card["id"],
            front=card["front"],
            back=card["back"],
            concept=concept,
            source="new",
            content_id=card.get("content_id"),
        ))

    actual_review = sum(1 for c in composed_cards if c.source == "review")
    actual_new = sum(1 for c in composed_cards if c.source == "new")

    rationale_parts = []
    if actual_review:
        rationale_parts.append(f"{actual_review} due for review")
    if actual_new:
        rationale_parts.append(f"{actual_new} new concepts")
    if not rationale_parts:
        rationale_parts.append("no cards available")

    return StudySessionResponse(
        id=session_id,
        type=req.type,
        cards=composed_cards,
        mix={"review": actual_review, "new": actual_new},
        rationale=" · ".join(rationale_parts),
    )


@router.post("/{session_id}/review")
async def submit_session_review(
    session_id: str,
    req: SessionReviewRequest,
    session: AsyncSession = Depends(get_session),
):
    """Submit study session results — publishes the StudySessionReviewed event.

    Unlike the single-deck flashcard review endpoint, this handles cards
    from multiple source decks (a composed session spans documents). Mastery
    updates, profile learning, and session tracking run as event reactions
    (app/events/handlers/study.py).
    """
    outcomes = [
        CardOutcome(
            card_id=r.card_id,
            concept=(r.concept or "").strip(),
            known=r.known,
            latency_secs=r.secs,
        )
        for r in req.results
        if (r.concept or "").strip()
    ]

    await bus.publish(
        StudySessionReviewed(
            session_id=session_id,
            duration_secs=req.duration_secs,
            results=outcomes,
        )
    )
    return {"recorded": len(outcomes), "session_id": session_id}
