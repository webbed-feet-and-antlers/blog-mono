"""Quiz attempt routes — submit answers, get scored."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import ContentItem, QuizAttempt
from ..schemas import QuizAttemptOut, QuizAttemptRequest

router = APIRouter(prefix="/api/quiz", tags=["quiz"])


@router.post("/{content_id}/attempt", response_model=QuizAttemptOut, status_code=201)
async def submit_quiz_attempt(
    content_id: str,
    req: QuizAttemptRequest,
    session: AsyncSession = Depends(get_session),
):
    item = await session.get(ContentItem, content_id)
    if item is None or item.type != "quiz":
        raise HTTPException(status_code=404, detail="Quiz not found")

    questions = item.content.get("questions", [])
    total = len(questions)
    correct = 0
    for q in questions:
        qid = q.get("id")
        answer_idx = q.get("answer_idx")
        if qid is not None and req.answers.get(qid) == answer_idx:
            correct += 1

    score = (correct / total) if total else 0.0
    attempt = QuizAttempt(
        id=uuid.uuid4().hex[:12],
        content_id=content_id,
        answers=req.answers,
        score=score,
        correct_count=correct,
        total_count=total,
    )
    session.add(attempt)
    await session.commit()
    await session.refresh(attempt)
    return attempt
