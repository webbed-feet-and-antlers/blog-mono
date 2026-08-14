"""Domain events — the vocabulary of "things that happened" in the app.

Routes commit their core write, then publish one of these events. Handlers
subscribed to an event perform the automatic reactions (mastery updates,
profile learning, weak-topic detection, background analysis, …).

Payloads must stay JSON-serializable (dataclasses) because the bus persists
them to the agent_events log table.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class QuestionOutcome:
    """One answered quiz question — the unit of concept-mastery tracking."""

    question_id: str
    prompt: str
    concept: str
    answered: bool
    is_correct: bool


@dataclass
class CardOutcome:
    """One reviewed flashcard — the other unit of concept-mastery tracking."""

    card_id: str
    concept: str
    known: bool


@dataclass
class DocumentIngested:
    """A document landed and is ready for the background ingestion chain.

    source="upload"  — text/PDF/office doc; text already extracted.
    source="audio"   — audio recording; needs transcription before analysis.
    """

    document_id: str
    source: str = "upload"


@dataclass
class DocumentAnalyzed:
    """The agent finished analyzing a document (rename + LLM analysis done)."""

    document_id: str
    analysis: dict


@dataclass
class QuizAttempted:
    """A quiz attempt was scored and persisted."""

    content_id: str
    document_id: str | None
    score: float
    total: int
    correct: int
    results: list[QuestionOutcome] = field(default_factory=list)


@dataclass
class FlashcardsReviewed:
    """A single-deck flashcard review was persisted."""

    content_id: str
    document_id: str | None
    results: list[CardOutcome] = field(default_factory=list)


@dataclass
class StudySessionReviewed:
    """A composed study session's results were persisted (cards span decks)."""

    session_id: str
    results: list[CardOutcome] = field(default_factory=list)


@dataclass
class GenerationCompleted:
    """The agent finished generating a ContentItem (any origin)."""

    document_id: str
    content_id: str
    task_type: str
    origin: str | None = None  # None (user-requested) | "auto" | "proactive"
