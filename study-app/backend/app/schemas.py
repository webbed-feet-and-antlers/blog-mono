"""Pydantic request/response schemas for the API."""

from __future__ import annotations

from datetime import date, datetime
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
    lesson_id: str | None = None
    module_id: str | None = None
    kind: str = "text"
    duration_seconds: int | None = None
    transcription_status: str | None = None
    topic: str | None = None

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
    concept: str


class QuizContent(BaseModel):
    title: str
    questions: list[QuizQuestion]  # type: ignore[valid-type]


class Flashcard(TypedDict):
    id: str
    front: str
    back: str
    concept: str
    variants: list[dict]


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
    # Parent document (eager-loaded). Present on list/detail responses.
    document: DocumentOut | None = None

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
    # Behavioral timing (optional — from the frontend tracker).
    duration_secs: float | None = Field(
        default=None, description="total time spent on the quiz"
    )
    question_timings: dict[str, float] | None = Field(
        default=None,
        description="per-question seconds from first render to final answer",
    )


# --- Flashcard reviews ---


class FlashcardReviewItem(BaseModel):
    card_id: str
    known: bool
    concept: str = ""
    # Seconds the card was studied before the decision (optional).
    secs: float | None = None


class FlashcardReviewRequest(BaseModel):
    results: list[FlashcardReviewItem] = Field(
        ..., description="per-card review outcomes"
    )


class FlashcardReviewResponse(BaseModel):
    recorded: int


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


# --- Event log (debug) ---


class AgentEventOut(BaseModel):
    id: str
    created_at: datetime
    event_type: str
    handler: str | None
    status: str
    payload: Any
    error: str | None

    model_config = {"from_attributes": True}


# --- Activity telemetry (in-app actions) ---


class ActivityEventIn(BaseModel):
    type: str = Field(..., description="dot-namespaced action type")
    ts: str | None = Field(default=None, description="client ISO timestamp")
    props: dict = Field(default_factory=dict)


class ActivityBatchIn(BaseModel):
    events: list[ActivityEventIn] = Field(default_factory=list)


# --- Study plans ---


class PlanItemOut(BaseModel):
    id: str
    type: str
    title: str
    rationale: str = ""
    day_offset: int = 0
    estimate_mins: int = 15
    status: str = "pending"  # pending | done
    done_at: str | None = None
    done_reason: str | None = None
    done_kind: str | None = None  # auto | manual
    target: dict = {}


class StudyPlanOut(BaseModel):
    id: str
    module_id: str
    version: int
    generated_at: datetime
    stale_reasons: list = []
    items: list[PlanItemOut] = []
    meta: dict = {}
    # Computed on read: whether the plan should regenerate and why.
    staleness: dict = {}

    model_config = {"from_attributes": True}


class PlanGenerateRequest(BaseModel):
    exam_date: date | None = None


class PlanItemPatch(BaseModel):
    status: Literal["done", "pending"]


# --- Modules & Lessons (organization hierarchy) ---


class ModuleCreate(BaseModel):
    title: str
    exam_date: date | None = None
    academic_year: str | None = None
    term: str | None = None


class ModuleUpdate(BaseModel):
    """Partial module update — only fields the client actually sent change
    (checked via model_fields_set), so a rename doesn't wipe the exam date
    or semester and vice versa."""
    title: str | None = None
    exam_date: date | None = None
    academic_year: str | None = None
    term: str | None = None


class LessonCreate(BaseModel):
    title: str


class ModuleOut(BaseModel):
    id: str
    title: str
    exam_date: date | None = None
    academic_year: str | None = None
    term: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class LessonOut(BaseModel):
    id: str
    title: str
    module_id: str
    created_at: datetime

    model_config = {"from_attributes": True}


class LessonWithDocs(LessonOut):
    documents: list[DocumentOut] = []


class ModuleWithTree(ModuleOut):
    lessons: list[LessonWithDocs] = []
    # Documents filed directly under the module (not inside a lesson).
    documents: list[DocumentOut] = []


class ModuleTreeResponse(BaseModel):
    modules: list[ModuleWithTree]
    unfiled: list[DocumentOut]


class DocumentMove(BaseModel):
    lesson_id: str | None = None
    module_id: str | None = None


# --- Lecture sessions ---


class SlideTimestamp(BaseModel):
    slide_number: int
    audio_seconds: float


class LectureSessionCreate(BaseModel):
    title: str
    lesson_id: str | None = None
    audio_doc_id: str | None = None
    slides_doc_id: str | None = None
    notes: str = ""
    duration_seconds: int = 0
    slide_timestamps: list[SlideTimestamp] = []
    slide_count: int = 0


class LectureSessionNotesUpdate(BaseModel):
    notes: str


class LectureSessionOut(BaseModel):
    id: str
    lesson_id: str | None = None
    title: str
    audio_doc_id: str | None = None
    slides_doc_id: str | None = None
    notes: str = ""
    duration_seconds: int = 0
    slide_timestamps: list[dict] = []
    slide_count: int = 0
    status: str = "completed"
    created_at: datetime

    model_config = {"from_attributes": True}


class LectureSessionDetail(LectureSessionOut):
    audio_doc: DocumentOut | None = None
    slides_doc: DocumentOut | None = None


# --- Knowledge graph (concepts + edges) ---


class ConceptCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    module_id: str | None = None


class ConceptEdgeCreate(BaseModel):
    """target is a concept NAME (concepts are name-keyed in the mastery
    store, so names are the stable handle the UI has)."""
    target: str = Field(min_length=1, max_length=200)
    relation: Literal["prerequisite", "part_of", "related"]
