"""Flashcard review route — persist 'I know this' / 'Still learning' clicks.

The route recovers concept tags from the stored cards (when the client
doesn't send them), commits nothing itself — the concept-mastery updates,
learner-profile learning, and recommendation session tracking all run as
reactions to the FlashcardsReviewed event (app/events/handlers/study.py).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..db import get_session
from ..events import bus
from ..events.domain import CardOutcome, FlashcardsReviewed
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
    user: str = Depends(get_current_user),
):
    item = await session.get(ContentItem, content_id)
    if item is None or item.type != "flashcards" or item.user_id != user:
        raise HTTPException(status_code=404, detail="Flashcard deck not found")

    # Build the outcome list — recovering the concept from the card itself
    # (tagged at generation time) when the client didn't send it.
    outcomes: list[CardOutcome] = []
    for result in req.results:
        concept = result.concept.strip()
        if not concept:
            card = next(
                (c for c in item.content.get("cards", []) if c.get("id") == result.card_id),
                None,
            )
            concept = (card or {}).get("concept", "")
        if concept:
            outcomes.append(
                CardOutcome(
                    card_id=result.card_id,
                    concept=concept,
                    known=result.known,
                    latency_secs=result.secs,
                )
            )

    await bus.publish(
        FlashcardsReviewed(
            content_id=content_id,
            document_id=item.document_id,
            results=outcomes,
        )
    )
    return FlashcardReviewResponse(recorded=len(outcomes))
