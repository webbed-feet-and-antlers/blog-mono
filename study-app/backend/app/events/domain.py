"""Domain events — the vocabulary of "things that happened" in the app.

Routes commit their core write, then publish one of these events. Handlers
subscribed to an event perform the automatic reactions (mastery updates,
profile learning, weak-topic detection, background analysis, …).

Payloads must stay JSON-serializable (dataclasses) because the bus persists
them to the agent_events log table.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..auth import user_ref_id


@dataclass
class UserEvent:
    """Base for events tied to a specific user's data.

    ``user_id`` captures the current owner at construction time — every
    publisher runs either inside a request (the auth dependency has set
    the contextvar) or inside a handler already scoped to the source
    event's owner. The bus re-applies it around handler runs so reactions
    write as the same user.
    """

    user_id: str = field(default="", kw_only=True)

    def __post_init__(self) -> None:
        if not self.user_id:
            self.user_id = user_ref_id()


@dataclass
class QuestionOutcome:
    """One answered quiz question — the unit of concept-mastery tracking."""

    question_id: str
    prompt: str
    concept: str
    answered: bool
    is_correct: bool
    # Seconds from question render to final answer (behavioral signal).
    latency_secs: float | None = None


@dataclass
class CardOutcome:
    """One reviewed flashcard — the other unit of concept-mastery tracking."""

    card_id: str
    concept: str
    known: bool
    latency_secs: float | None = None


@dataclass
class DocumentIngested(UserEvent):
    """A document landed and is ready for the background ingestion chain.

    source="upload"  — text/PDF/office doc; text already extracted.
    source="audio"   — audio recording; needs transcription before analysis.
    """

    document_id: str
    source: str = "upload"


@dataclass
class DocumentAnalyzed(UserEvent):
    """The agent finished analyzing a document (rename + LLM analysis done)."""

    document_id: str
    analysis: dict


@dataclass
class QuizAttempted(UserEvent):
    """A quiz attempt was scored and persisted."""

    content_id: str
    document_id: str | None
    score: float
    total: int
    correct: int
    duration_secs: float | None = None
    results: list[QuestionOutcome] = field(default_factory=list)


@dataclass
class FlashcardsReviewed(UserEvent):
    """A single-deck flashcard review was persisted."""

    content_id: str
    document_id: str | None
    results: list[CardOutcome] = field(default_factory=list)


@dataclass
class StudySessionReviewed(UserEvent):
    """A composed study session's results were persisted (cards span decks)."""

    session_id: str
    duration_secs: float | None = None
    results: list[CardOutcome] = field(default_factory=list)


@dataclass
class GenerationCompleted(UserEvent):
    """The agent finished generating a ContentItem (any origin)."""

    document_id: str
    content_id: str
    task_type: str
    origin: str | None = None  # None (user-requested) | "auto" | "proactive"


# --- Behavioral telemetry (in-app actions as memory) ------------------------


@dataclass
class ActivityEntry:
    """One user action captured by the frontend tracker.

    ts is an ISO string from the client when available (event time); the
    handler fills it with server time when missing.
    """

    type: str  # dot-namespaced, e.g. "document.opened", "quiz.answered"
    ts: str = ""
    props: dict = field(default_factory=dict)


@dataclass
class ActivitiesLogged(UserEvent):
    """A flushed batch of frontend activity events.

    One publish per flush (not per event) keeps the agent_events log quiet —
    the ledger rows live in user_activities instead.
    """

    entries: list[ActivityEntry] = field(default_factory=list)


# --- Study plans (adaptive, module-scoped) ----------------------------------


@dataclass
class StudyPlanStaleDetected(UserEvent):
    """A module's study plan no longer matches reality (new content analyzed,
    quiz results). The regen handler regenerates it behind a per-module
    cooldown so a semester's uploads don't burn unbounded LLM calls."""

    module_id: str
    reason: str
