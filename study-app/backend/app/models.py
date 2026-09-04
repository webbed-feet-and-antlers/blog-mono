"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import date, datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Date, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# Dialect-portable column types. datetime columns are timezone-aware —
# asyncpg refuses naive columns fed tz-aware values (and SQLite ignores
# the flag, so dev/test behave identically). JSON payloads become JSONB
# on Postgres (compact, indexable) and stay plain JSON on SQLite.
_TZDateTime = DateTime(timezone=True)
_JsonCol = JSON().with_variant(JSONB(), "postgresql")


# Owner column shared by every user-specific table: the Clerk user id of
# the request that created the row ("" for rows created by ambient
# contexts — tests/evals — which run as the implicit default user).
# Declared per-table rather than via a mixin to keep each model explicit.


class Module(Base):
    """A course module (e.g. 'BIO201 - Cell Biology')."""

    __tablename__ = "modules"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, default="", index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    # Optional target/exam date — paces the module's study plan.
    exam_date: Mapped[date | None] = mapped_column(Date, nullable=True, default=None)
    # Semester organization. academic_year like "2026/27"; term like
    # "Autumn" | "Spring" | "Summer" (or any label). Both nullable — unfiled
    # modules surface in the "No semester set" group until assigned.
    academic_year: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    term: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(_TZDateTime, default=_utcnow)

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
    created_at: Mapped[datetime] = mapped_column(_TZDateTime, default=_utcnow)

    module: Mapped["Module"] = relationship(back_populates="lessons")
    documents: Mapped[list["Document"]] = relationship(back_populates="lesson")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, default="", index=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    mime: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(Text, default="")
    page_count: Mapped[int] = mapped_column(default=0)
    char_count: Mapped[int] = mapped_column(default=0)
    uploaded_at: Mapped[datetime] = mapped_column(_TZDateTime, default=_utcnow)
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
    # Denormalized from the parent Document at creation so deck/quiz scans
    # (the session composer, build_context) filter by owner without a join.
    user_id: Mapped[str] = mapped_column(String, nullable=False, default="", index=True)
    type: Mapped[str] = mapped_column(String, nullable=False)  # notes|quiz|flashcards
    # Structured payload whose shape depends on `type`:
    #   notes      -> {markdown: str}
    #   quiz       -> {title, questions: [{id, prompt, options[], answer_idx,
    #                 explanation, concept}]}
    #   flashcards -> {title, cards: [{id, front, back, concept,
    #                 variants: [{front, back}]}]}
    # The `concept` field on each question/card links it to a per-concept FSRS
    # mastery entry, so quiz + flashcard outcomes share one mastery store.
    content: Mapped[dict] = mapped_column(_JsonCol, default=dict)
    created_at: Mapped[datetime] = mapped_column(_TZDateTime, default=_utcnow)

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
    user_id: Mapped[str] = mapped_column(String, nullable=False, default="", index=True)
    # answers: {question_id: selected_option_index}
    answers: Mapped[dict] = mapped_column(_JsonCol, default=dict)
    score: Mapped[float] = mapped_column(default=0.0)  # 0..1 fraction correct
    correct_count: Mapped[int] = mapped_column(default=0)
    total_count: Mapped[int] = mapped_column(default=0)
    taken_at: Mapped[datetime] = mapped_column(_TZDateTime, default=_utcnow)

    content_item: Mapped[ContentItem] = relationship(back_populates="quiz_attempts")


class AgentMemory(Base):
    """Key/value memory the agent reads and writes across runs.

    scope "doc"  -> keyed by document_id (analysis cache, extracted concepts)
    scope "user" -> cross-document learnings (style prefs, weak topics);
                    ref_id holds the owner's user id ("" = ambient default)
    """

    __tablename__ = "agent_memory"
    __table_args__ = (
        # Upsert target: one value per (scope, ref_id, key).
        UniqueConstraint("scope", "ref_id", "key", name="uq_agent_memory_scope_ref_key"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    scope: Mapped[str] = mapped_column(String, nullable=False)  # doc|user
    ref_id: Mapped[str] = mapped_column(String, nullable=False, default="")
    # Owner stamped on write from the request context — lets doc-scope
    # listings (get_doc_topics) filter per user without joining documents.
    user_id: Mapped[str] = mapped_column(String, nullable=False, default="", index=True)
    key: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[dict] = mapped_column(_JsonCol, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        _TZDateTime, default=_utcnow, onupdate=_utcnow
    )


class RecommendationEvent(Base):
    """Telemetry events for the recommendation engine.

    Tracks impressions (when a recommendation is shown) and interactions
    (clicks, dismissals, completions). Feeds the LinUCB bandit for weight
    optimization.
    """

    __tablename__ = "recommendation_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, default="", index=True)
    impression_id: Mapped[str] = mapped_column(String, index=True)
    strategy_name: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, default="")
    document_id: Mapped[str | None] = mapped_column(String, nullable=True)
    score: Mapped[float] = mapped_column(default=0.0)
    rank: Mapped[int] = mapped_column(default=0)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    reward: Mapped[float | None] = mapped_column(nullable=True)
    context_snapshot: Mapped[dict] = mapped_column(_JsonCol, default=dict)
    created_at: Mapped[datetime] = mapped_column(_TZDateTime, default=_utcnow)


class AgentEvent(Base):
    """Append-only log of every domain event published on the event bus.

    One "dispatch" row per publish (handler=NULL), plus one row per handler
    run with its outcome — so every automatic action the agent takes is
    visible and debuggable via GET /api/events.
    """

    __tablename__ = "agent_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(_TZDateTime, default=_utcnow, index=True)
    # Whose action triggered the event (nullable — some events are global).
    user_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # Event class name, e.g. "QuizAttempted", "DocumentIngested".
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    # "module.function" of the handler; NULL for dispatch rows.
    handler: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="ok")  # ok | failed
    payload: Mapped[dict] = mapped_column(_JsonCol, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class UserActivity(Base):
    """Append-only ledger of in-app user actions — the behavioral raw
    material the agent distills into engagement stats, study patterns, and
    (via LLM reflection) learner insights.

    Written by the ActivitiesLogged event handler; read by the reflection
    job and the profile endpoint.
    """

    __tablename__ = "user_activities"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, default="", index=True)
    ts: Mapped[datetime] = mapped_column(_TZDateTime, default=_utcnow, index=True)
    # Dot-namespaced action type, e.g. "document.opened", "quiz.answered".
    type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # Type-specific payload, e.g. {document_id, tab, dwell_secs}.
    props: Mapped[dict] = mapped_column(_JsonCol, default=dict)


class StudyPlan(Base):
    """The agent-generated study plan for one module (one active plan per
    module per user, versioned in place).

    items JSON shape (per item):
      {id, type, title, rationale, day_offset, estimate_mins,
       status: pending|done, done_at, done_reason, done_kind: auto|manual,
       target: {module_id?, document_id?, concepts?}}
    Types: review_concepts | take_quiz | generate_quiz | review_deck |
           generate_flashcards | read_document
    """

    __tablename__ = "study_plans"
    __table_args__ = (
        # One active plan per (user, module).
        UniqueConstraint("user_id", "module_id", name="uq_study_plans_user_module"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    module_id: Mapped[str] = mapped_column(
        ForeignKey("modules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False, default="", index=True)
    version: Mapped[int] = mapped_column(default=1)
    generated_at: Mapped[datetime] = mapped_column(_TZDateTime, default=_utcnow)
    # Staleness signals appended by event handlers ("new document analyzed",
    # "quiz results") — cleared on regeneration.
    stale_reasons: Mapped[list] = mapped_column(_JsonCol, default=list)
    items: Mapped[list] = mapped_column(_JsonCol, default=list)
    meta: Mapped[dict] = mapped_column(_JsonCol, default=dict)


class LectureSession(Base):
    """A lecture recording session — groups audio + slides + notes + timestamps.

    Belongs to a Lesson (optional). References the audio Document (recording)
    and slides Document (PDF). Owns the slide↔audio timestamp mapping and
    user-authored notes.
    """

    __tablename__ = "lecture_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, default="", index=True)
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
    slide_timestamps: Mapped[list] = mapped_column(_JsonCol, default=list)
    slide_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String, default="completed")
    created_at: Mapped[datetime] = mapped_column(_TZDateTime, default=_utcnow)


# --- Knowledge / skill graph -------------------------------------------------
#
# Concepts as first-class rows (rather than strings inside the mastery blob)
# plus typed edges between them. The mastery store's per-concept FSRS state
# stays in agent_memory; these tables own the *structure* — what depends on
# what — which the planner traverses and the UI renders.


class Concept(Base):
    """A named concept in the user's knowledge graph.

    Names are the join key against the mastery store (quiz questions and
    flashcards tag items by concept name), so (user_id, name) is unique.
    """

    __tablename__ = "concepts"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_concepts_user_name"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, default="", index=True)
    # Which module the concept was extracted under, when known. Nullable —
    # cross-module concepts are legitimate.
    module_id: Mapped[str | None] = mapped_column(
        ForeignKey("modules.id", ondelete="SET NULL"), nullable=True, default=None
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(_TZDateTime, default=_utcnow)

    outgoing_edges: Mapped[list["ConceptEdge"]] = relationship(
        foreign_keys="ConceptEdge.source_id",
        back_populates="source",
        cascade="all, delete-orphan",
    )
    incoming_edges: Mapped[list["ConceptEdge"]] = relationship(
        foreign_keys="ConceptEdge.target_id",
        back_populates="target",
        cascade="all, delete-orphan",
    )


class ConceptEdge(Base):
    """A typed, directed relationship between two of the user's concepts.

    relation: "prerequisite" (target must be understood before source),
    "part_of" (source is a component of target), or "related" (weaker link).
    """

    __tablename__ = "concept_edges"
    __table_args__ = (
        UniqueConstraint("source_id", "target_id", "relation", name="uq_concept_edges_str"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_id: Mapped[str] = mapped_column(
        ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(_TZDateTime, default=_utcnow)

    source: Mapped["Concept"] = relationship(
        foreign_keys="ConceptEdge.source_id", back_populates="outgoing_edges"
    )
    target: Mapped["Concept"] = relationship(
        foreign_keys="ConceptEdge.target_id", back_populates="incoming_edges"
    )


class DocumentChunk(Base):
    """A chunk of document text with its embedding — the retrieval pillar.

    The table (and the pgvector extension) only exists on Postgres; the
    embeddings pipeline that fills it is a follow-up. 384 dims matches the
    in-process fastembed BGE-small model — the table starts empty, so
    changing the model later is a re-embed, not a rescue.
    """

    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalized owner, like content_items — retrieval filters by user.
    user_id: Mapped[str] = mapped_column(String, nullable=False, default="", index=True)
    chunk_index: Mapped[int] = mapped_column(default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    embedding = mapped_column(Vector(384))
    created_at: Mapped[datetime] = mapped_column(_TZDateTime, default=_utcnow)
