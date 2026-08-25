"""Reactions to study activity (quiz attempts, flashcard/study reviews).

This is the de-duplicated version of the side-effect trio that used to be
copy-pasted across quiz.py, flashcards.py, and study_session.py with
slightly different shapes:

    update_concept_mastery → update_learner_profile → record_action

Each reaction is now one handler subscribed to the domain events. Adding a
new automatic behavior to "the student studied" means adding a handler here
— not editing every route.
"""

from __future__ import annotations

import logging
from typing import Union

from sqlalchemy.ext.asyncio import AsyncSession

from ...agent import behavior
from ...agent import memory as memory_store
from ...config import settings
from ...recommend.session import record_action
from .. import bus
from ..domain import (
    CardOutcome,
    FlashcardsReviewed,
    QuestionOutcome,
    QuizAttempted,
    StudySessionReviewed,
)

logger = logging.getLogger(__name__)

StudyEvent = Union[QuizAttempted, FlashcardsReviewed, StudySessionReviewed]


def _outcome_correct(outcome: Union[QuestionOutcome, CardOutcome]) -> bool:
    if isinstance(outcome, QuestionOutcome):
        return outcome.is_correct
    return outcome.known


# --- Reaction 1: per-concept mastery + FSRS scheduling (always on) ----------


@bus.on(QuizAttempted, FlashcardsReviewed, StudySessionReviewed)
async def update_mastery(event: StudyEvent, session: AsyncSession) -> None:
    """Record each answered question / reviewed card against its concept tag.

    Both right AND wrong outcomes feed the tally — mastery reflects correct
    answers too, not just misses. FSRS scheduling and rolling answer latency
    happen inside update_concept_mastery.
    """
    recorded = 0
    for outcome in event.results:
        concept = (outcome.concept or "").strip()
        if not concept:
            continue
        await memory_store.update_concept_mastery(
            session,
            concept,
            correct=_outcome_correct(outcome),
            latency_secs=outcome.latency_secs,
        )
        recorded += 1
    if recorded:
        logger.info(
            "[events] mastery: %d outcome(s) recorded from %s",
            recorded,
            type(event).__name__,
        )


# --- Reaction 2: learner profile (who the learner is) -----------------------


@bus.on(QuizAttempted)
async def update_profile_from_quiz(event: QuizAttempted, session: AsyncSession) -> None:
    """Feed the quiz score + doc difficulty into the learner profile so the
    agent can calibrate level, preferred difficulty, and formats. The quiz's
    duration feeds the study-pattern history (pacing signal)."""
    doc_difficulty: str | None = None
    if event.document_id:
        analysis = await memory_store.read_memory(
            session, "doc", event.document_id, "analysis"
        )
        doc_difficulty = (analysis or {}).get("difficulty") if analysis else None
    await memory_store.update_learner_profile(
        session, quiz_score=event.score, doc_difficulty=doc_difficulty
    )
    if event.duration_secs:
        await behavior.record_quiz_duration(session, event.duration_secs, event.score)


@bus.on(FlashcardsReviewed, StudySessionReviewed)
async def update_profile_from_cards(
    event: Union[FlashcardsReviewed, StudySessionReviewed],
    session: AsyncSession,
) -> None:
    """Feed flashcard review results into the profile (known-ratio stat).
    Completed study sessions also count toward the completion-rate pattern."""
    if not event.results:
        return
    await memory_store.update_learner_profile(
        session,
        flashcard_results=[
            {"known": r.known, "concept": r.concept} for r in event.results
        ],
    )
    if isinstance(event, StudySessionReviewed):
        await behavior.record_study_session_completed(session)


# --- Reaction 3: session tracking (recommendation chaining + fatigue) -------


@bus.on(QuizAttempted, FlashcardsReviewed, StudySessionReviewed)
async def record_activity(event: StudyEvent, session: AsyncSession) -> None:
    """Record the completed action so recommendations can chain ("you just
    did flashcards → try a quiz") and pace themselves (fatigue)."""
    if isinstance(event, QuizAttempted):
        await record_action(session, "quiz", event.document_id)
    elif isinstance(event, FlashcardsReviewed):
        await record_action(session, "flashcards", event.document_id)
    else:
        # Composed study sessions span documents — no single doc_id.
        if not event.results:
            return
        await record_action(session, "flashcards")


# --- Reaction 4: weak-topic detection (quiz misses → proactive signal) ------


@bus.on(QuizAttempted)
async def detect_weak_topics(event: QuizAttempted, session: AsyncSession) -> None:
    """Turn quiz misses into weak-topic memory — but only when the learner
    actually struggled. Don't pollute memory with "weak" topics from quizzes
    they aced."""
    if not (
        settings.proactive_enabled
        and event.total > 0
        and event.score < settings.proactive_score_threshold
    ):
        return

    missed_prompts = [
        o.prompt for o in event.results if o.answered and not o.is_correct
    ]
    if not missed_prompts or not event.document_id:
        return

    analysis = await memory_store.read_memory(
        session, "doc", event.document_id, "analysis"
    )
    concepts = (analysis or {}).get("concepts") or []
    weak = _match_concepts(missed_prompts, concepts)
    if weak:
        await memory_store.add_weak_topics(session, weak)
        logger.info(
            "[events] quiz feedback: %d miss(es) -> weak topics: %s",
            len(missed_prompts),
            weak,
        )

    # Record the attempt count on the doc so future generations know.
    prior_attempts = (
        await memory_store.read_memory(
            session, "doc", event.document_id, "quiz_attempts"
        )
    ) or 0
    await memory_store.write_memory(
        session, "doc", event.document_id, "quiz_attempts", int(prior_attempts) + 1
    )


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
