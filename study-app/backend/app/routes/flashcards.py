"""Flashcard review routes — persist 'I know this' / 'Still learning' clicks.

This closes the loop on flashcard interactions: previously the known/unknown
state was ephemeral (lost on unmount). Now each review feeds the per-concept
mastery tally, so the agent learns what the student has mastered from
flashcards too — not just quizzes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent import memory as memory_store
from ..db import get_session
from ..models import ContentItem
from ..schemas import FlashcardReviewRequest, FlashcardReviewResponse

router = APIRouter(prefix="/api/flashcards", tags=["flashcards"])


@router.post(
    "/{content_id}/review", response_model=FlashcardReviewResponse, status_code=201
)
async def submit_flashcard_review(
    content_id: str,
    req: FlashcardReviewRequest,
    session: AsyncSession = Depends(get_session),
):
    item = await session.get(ContentItem, content_id)
    if item is None or item.type != "flashcards":
        raise HTTPException(status_code=404, detail="Flashcard deck not found")

    recorded = 0
    for result in req.results:
        concept = result.concept.strip()
        if not concept:
            # Try to recover the concept from the card itself (tagged at
            # generation time) if the client didn't send it.
            card = next(
                (c for c in item.content.get("cards", []) if c.get("id") == result.card_id),
                None,
            )
            concept = (card or {}).get("concept", "")
        if concept:
            await memory_store.update_concept_mastery(
                session, concept, correct=result.known
            )
            recorded += 1

    # Feed the flashcard review results into the learner profile.
    if req.results:
        await memory_store.update_learner_profile(
            session,
            flashcard_results=[
                {"known": r.known, "concept": r.concept} for r in req.results
            ],
        )

    # Record session action for recommendation chaining + fatigue.
    from ..recommend.session import record_action

    await record_action(session, "flashcards", item.document_id)

    await session.commit()
    return FlashcardReviewResponse(recorded=recorded)
