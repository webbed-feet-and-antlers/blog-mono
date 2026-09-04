"""Quiz attempt route — score the attempt, persist it, publish the event.

The route is deliberately thin: parse, score, insert, commit, publish.
Everything that should happen *because* a quiz was taken (concept-mastery
updates, learner-profile learning, weak-topic detection, recommendation
session tracking) lives in event handlers — see app/events/handlers/study.py.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..db import get_session
from ..events import bus
from ..events.domain import QuestionOutcome, QuizAttempted
from ..models import ContentItem, QuizAttempt
from ..schemas import QuizAttemptOut, QuizAttemptRequest

router = APIRouter(prefix="/api/quiz", tags=["quiz"])
logger = logging.getLogger(__name__)


@router.post("/{content_id}/attempt", response_model=QuizAttemptOut, status_code=201)
async def submit_quiz_attempt(
    content_id: str,
    req: QuizAttemptRequest,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    item = await session.get(ContentItem, content_id)
    if item is None or item.type != "quiz" or item.user_id != user:
        raise HTTPException(status_code=404, detail="Quiz not found")

    questions = item.content.get("questions", [])
    total = len(questions)
    correct = 0
    results: list[QuestionOutcome] = []
    timings = req.question_timings or {}

    for q in questions:
        qid = q.get("id")
        answer_idx = q.get("answer_idx")
        answered = qid is not None and qid in req.answers
        # Only a submitted answer can be correct (guards the edge case of a
        # question with a missing answer_idx counting a blank as correct).
        is_correct = answered and req.answers.get(qid) == answer_idx
        if is_correct:
            correct += 1
        results.append(
            QuestionOutcome(
                question_id=qid or "",
                prompt=q.get("prompt", ""),
                concept=q.get("concept", ""),
                answered=answered,
                is_correct=is_correct,
                latency_secs=timings.get(qid) if qid else None,
            )
        )

    score = (correct / total) if total else 0.0
    attempt = QuizAttempt(
        id=uuid.uuid4().hex[:12],
        content_id=content_id,
        user_id=user,
        answers=req.answers,
        score=score,
        correct_count=correct,
        total_count=total,
    )
    session.add(attempt)
    await session.commit()
    await session.refresh(attempt)

    # Post-commit: the attempt is durable; reactions run isolated on the bus
    # and can no longer roll it back (or 500 the submit) if one of them fails.
    await bus.publish(
        QuizAttempted(
            content_id=content_id,
            document_id=item.document_id,
            score=score,
            total=total,
            correct=correct,
            duration_secs=req.duration_secs,
            results=results,
        )
    )
    return attempt
