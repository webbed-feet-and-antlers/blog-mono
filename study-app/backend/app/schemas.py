"""Pydantic request/response schemas for the API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


# --- Document ---


class DocumentOut(BaseModel):
    id: str
    filename: str
    mime: str
    page_count: int
    char_count: int
    uploaded_at: datetime

    model_config = {"from_attributes": True}


class DocumentDetail(DocumentOut):
    text: str


# --- Content items ---


class QuizQuestion(TypedDict):
    id: str
    prompt: str
    options: list[str]
    answer_idx: int
    explanation: str


class QuizContent(BaseModel):
    title: str
    questions: list[QuizQuestion]  # type: ignore[valid-type]


class Flashcard(TypedDict):
    id: str
    front: str
    back: str


class FlashcardContent(BaseModel):
    title: str
    cards: list[Flashcard]  # type: ignore[valid-type]


class NotesContent(BaseModel):
    markdown: str


class ContentItemOut(BaseModel):
    id: str
    document_id: str
    type: Literal["notes", "quiz", "flashcards"]
    content: dict
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Generation ---


class GenerateRequest(BaseModel):
    document_id: str
    task_type: Literal["notes", "quiz", "flashcards"]
    # Optional hints the user can pass to steer the agent.
    instructions: str | None = None


# --- Quiz attempts ---


class QuizAttemptRequest(BaseModel):
    answers: dict[str, int] = Field(
        ..., description="mapping of question_id -> selected option index"
    )


class QuizAttemptOut(BaseModel):
    id: str
    content_id: str
    score: float
    correct_count: int
    total_count: int
    answers: dict
    taken_at: datetime

    model_config = {"from_attributes": True}


# --- Agent memory (debug) ---


class AgentMemoryOut(BaseModel):
    id: str
    scope: str
    ref_id: str
    key: str
    value: Any
    updated_at: datetime

    model_config = {"from_attributes": True}
