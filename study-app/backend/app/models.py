"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Module(Base):
    """A course module (e.g. 'BIO201 - Cell Biology')."""

    __tablename__ = "modules"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    lessons: Mapped[list["Lesson"]] = relationship(
        back_populates="module", cascade="all, delete-orphan"
    )
    # Documents filed directly under the module (not inside a lesson) — e.g.
    # a textbook or reading list item. Distinct from lesson-scoped docs.
    documents: Mapped[list["Document"]] = relationship(
        back_populates="module"
    )


class Lesson(Base):
    """A lesson within a module (e.g. 'Week 5 - Respiration')."""

    __tablename__ = "lessons"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    module_id: Mapped[str] = mapped_column(
        ForeignKey("modules.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    module: Mapped["Module"] = relationship(back_populates="lessons")
    documents: Mapped[list["Document"]] = relationship(back_populates="lesson")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    mime: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(Text, default="")
    page_count: Mapped[int] = mapped_column(default=0)
    char_count: Mapped[int] = mapped_column(default=0)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    # Document kind: "text" (PDF/TXT/MD) or "audio" (lecture recording).
    kind: Mapped[str] = mapped_column(String, default="text")
    # Audio-specific fields (nullable, only set when kind == "audio").
    duration_seconds: Mapped[int | None] = mapped_column(nullable=True, default=None)
    transcription_status: Mapped[str | None] = mapped_column(
        String, nullable=True, default=None
    )  # None | "pending" | "transcribing" | "done" | "failed"
    transcription_error: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None
    )
    # Optional: which lesson this document belongs to. Nullable so existing
    # flat documents keep working (NULL = unfiled).
    lesson_id: Mapped[str | None] = mapped_column(
        ForeignKey("lessons.id", ondelete="SET NULL"), nullable=True, default=None
    )
    # Optional: which module this document is filed under directly (not via
    # a lesson). A doc is unfiled when BOTH lesson_id and module_id are NULL.
    # Mutual exclusivity is enforced at the app layer.
    module_id: Mapped[str | None] = mapped_column(
        ForeignKey("modules.id", ondelete="SET NULL"), nullable=True, default=None
    )

    content_items: Mapped[list[ContentItem]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    lesson: Mapped["Lesson | None"] = relationship(back_populates="documents")
    module: Mapped["Module | None"] = relationship(back_populates="documents")


class ContentItem(Base):
    """A piece of AI-generated content: notes, a quiz, or a flashcard deck."""

    __tablename__ = "content_items"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String, nullable=False)  # notes|quiz|flashcards
    # Structured payload whose shape depends on `type`:
    #   notes      -> {markdown: str}
    #   quiz       -> {title, questions: [{id, prompt, options[], answer_idx,
    #                 explanation, concept}]}
    #   flashcards -> {title, cards: [{id, front, back, concept,
    #                 variants: [{front, back}]}]}
    # The `concept` field on each question/card links it to a per-concept FSRS
    # mastery entry, so quiz + flashcard outcomes share one mastery store.
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    document: Mapped[Document] = relationship(back_populates="content_items")
    quiz_attempts: Mapped[list[QuizAttempt]] = relationship(
        back_populates="content_item", cascade="all, delete-orphan"
    )


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    content_id: Mapped[str] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False
    )
    # answers: {question_id: selected_option_index}
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    score: Mapped[float] = mapped_column(default=0.0)  # 0..1 fraction correct
    correct_count: Mapped[int] = mapped_column(default=0)
    total_count: Mapped[int] = mapped_column(default=0)
    taken_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    content_item: Mapped[ContentItem] = relationship(back_populates="quiz_attempts")


class AgentMemory(Base):
    """Key/value memory the agent reads and writes across runs.

    scope "doc"  -> keyed by document_id (analysis cache, extracted concepts)
    scope "user" -> cross-document learnings (style prefs, weak topics)
    """

    __tablename__ = "agent_memory"
    __table_args__ = (
        # Upsert target: one value per (scope, ref_id, key).
        UniqueConstraint("scope", "ref_id", "key", name="uq_agent_memory_scope_ref_key"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    scope: Mapped[str] = mapped_column(String, nullable=False)  # doc|user
    ref_id: Mapped[str] = mapped_column(String, nullable=False, default="")
    key: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class RecommendationEvent(Base):
    """Telemetry events for the recommendation engine.

    Tracks impressions (when a recommendation is shown) and interactions
    (clicks, dismissals, completions). Feeds the LinUCB bandit for weight
    optimization.
    """

    __tablename__ = "recommendation_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    impression_id: Mapped[str] = mapped_column(String, index=True)
    strategy_name: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, default="")
    document_id: Mapped[str | None] = mapped_column(String, nullable=True)
    score: Mapped[float] = mapped_column(default=0.0)
    rank: Mapped[int] = mapped_column(default=0)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    reward: Mapped[float | None] = mapped_column(nullable=True)
    context_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class AgentEvent(Base):
    """Append-only log of every domain event published on the event bus.

    One "dispatch" row per publish (handler=NULL), plus one row per handler
    run with its outcome — so every automatic action the agent takes is
    visible and debuggable via GET /api/events.
    """

    __tablename__ = "agent_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    # Event class name, e.g. "QuizAttempted", "DocumentIngested".
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    # "module.function" of the handler; NULL for dispatch rows.
    handler: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="ok")  # ok | failed
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class LectureSession(Base):
    """A lecture recording session — groups audio + slides + notes + timestamps.

    Belongs to a Lesson (optional). References the audio Document (recording)
    and slides Document (PDF). Owns the slide↔audio timestamp mapping and
    user-authored notes.
    """

    __tablename__ = "lecture_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    lesson_id: Mapped[str | None] = mapped_column(
        ForeignKey("lessons.id", ondelete="SET NULL"), nullable=True, default=None
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    audio_doc_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, default=None
    )
    slides_doc_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, default=None
    )
    notes: Mapped[str] = mapped_column(Text, default="")
    duration_seconds: Mapped[int] = mapped_column(default=0)
    slide_timestamps: Mapped[list] = mapped_column(JSON, default=list)
    slide_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String, default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
