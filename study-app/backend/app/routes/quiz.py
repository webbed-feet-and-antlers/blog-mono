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
        is_correct = qid is not None and req.answers.get(qid) == answer_idx
        answered = qid is not None and qid in req.answers
        if is_correct:
            correct += 1
        elif answered:
            # Missed — record the question prompt so we can concept-match
            # for the (legacy) weak-topics feedback loop below.
            missed_concepts.append(q.get("prompt", ""))

        # --- Always-on mastery tracking (both right AND wrong) ---
        # Each question now carries a 'concept' tag from generation time.
        # Record the outcome so the agent can customize future generations.
        concept = q.get("concept", "")
        if answered and concept:
            await memory_store.update_concept_mastery(
                session, concept, correct=is_correct
            )

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

    # --- Learner profile update (always on) ---
    # Feed the quiz score + doc difficulty into the profile so the agent can
    # calibrate level, preferred difficulty, and formats for future generations.
    doc_id = item.document_id
    analysis = await memory_store.read_memory(
        session, "doc", doc_id, "analysis"
    )
    doc_difficulty = (analysis or {}).get("difficulty") if analysis else None
    await memory_store.update_learner_profile(
        session, quiz_score=score, doc_difficulty=doc_difficulty
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
