"""Quiz attempt routes — submit answers, get scored, feed misses to memory.

This is where the agent's "weak topics" signal is born: every missed question
is matched to a concept from the document's analysis and written to user-scoped
memory, which the flashcard generator already reads (and weights toward).
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent import memory as memory_store
from ..config import settings
from ..db import get_session
from ..models import ContentItem, QuizAttempt
from ..schemas import QuizAttemptOut, QuizAttemptRequest

router = APIRouter(prefix="/api/quiz", tags=["quiz"])
logger = logging.getLogger(__name__)


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
    missed_concepts: list[str] = []

    for q in questions:
        qid = q.get("id")
        answer_idx = q.get("answer_idx")
        if qid is not None and req.answers.get(qid) == answer_idx:
            correct += 1
        elif qid is not None and qid in req.answers:
            # Missed — record the question prompt so we can concept-match.
            missed_concepts.append(q.get("prompt", ""))

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

    # --- Feedback loop: turn misses into weak-topic memory -----------------
    # Only track when the learner actually struggled (below the threshold).
    # Don't pollute memory with "weak" topics from quizzes they aced.
    if (
        settings.proactive_enabled
        and total > 0
        and score < settings.proactive_score_threshold
        and missed_concepts
    ):
        doc_id = item.document_id
        analysis = await memory_store.read_memory(
            session, "doc", doc_id, "analysis"
        )
        concepts = (analysis or {}).get("concepts") or []
        weak = _match_concepts(missed_concepts, concepts)
        if weak:
            await memory_store.add_weak_topics(session, weak)
            logger.info(
                "[agent] quiz feedback: %d miss(es) -> weak topics: %s",
                len(missed_concepts),
                weak,
            )

        # Record the attempt count on the doc so future generations know.
        prior_attempts = (
            await memory_store.read_memory(session, "doc", doc_id, "quiz_attempts")
        ) or 0
        await memory_store.write_memory(
            session, "doc", doc_id, "quiz_attempts", int(prior_attempts) + 1
        )

    await session.commit()
    await session.refresh(attempt)
    return attempt


def _match_concepts(missed_prompts: list[str], concepts: list[str]) -> list[str]:
    """Match missed-question prompts against the document's known concepts.

    Concepts are short phrases (e.g. "Calvin cycle", "RuBisCO", "photolysis").
    A concept is flagged weak if it appears (case-insensitive) in any missed
    question's prompt. Falls back to empty if concepts aren't available.
    """
    if not concepts:
        return []
    matched: list[str] = []
    seen = set()
    for prompt in missed_prompts:
        prompt_lower = prompt.lower()
        for concept in concepts:
            concept_str = str(concept).strip()
            if not concept_str or concept_str.lower() in seen:
                continue
            # Match multi-word concepts as a phrase, single words as substring.
            if concept_str.lower() in prompt_lower:
                matched.append(concept_str)
                seen.add(concept_str.lower())
    return matched
