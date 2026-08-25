"""Agent state — the shared data flowing through the LangGraph.

Every feature (notes/quiz/flashcards) runs through the same graph and shares
this state, which is what lets a single memory store + validation strategy
serve all features.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

TaskType = Literal["notes", "quiz", "flashcards"]


class AgentState(TypedDict, total=False):
    # Inputs
    document_id: str
    document_text: str
    task_type: TaskType
    instructions: str | None  # optional user steering hints
    session: Any  # AsyncSession passed through for memory + persistence

    # Pipeline outputs (filled by each node)
    analysis: dict[str, Any]   # from analyze_document — topic, concepts, difficulty
    plan: dict[str, Any]       # from plan — what to generate, structure
    memory: dict[str, Any]     # from retrieve_memory — prior learnings/context
    output: dict[str, Any]     # from generate — the feature content payload
    validation: dict[str, Any]  # from validate — pass/fail + reasons
    content_item: dict[str, Any]  # from finalize — persisted ContentItem fields

    # Diagnostics
    messages: list[str]        # human-readable step trace for debugging
    error: str | None
