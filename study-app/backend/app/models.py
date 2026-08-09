"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


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

    content_items: Mapped[list[ContentItem]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


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
    #   quiz       -> {title, questions: [{id, prompt, options[], answer_idx, explanation}]}
    #   flashcards -> {title, cards: [{id, front, back}]}
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
