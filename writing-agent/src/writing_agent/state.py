"""WritingState — the data flowing through the LangGraph."""

from __future__ import annotations

from typing import Any, TypedDict


class WritingState(TypedDict, total=False):
    # Inputs
    topic: str
    audience: str
    persona: str  # raw persona markdown from config/personas/
    raw_research: str
    style_reference_paths: list[str]

    # Pipeline artifacts
    outline: dict[str, Any]  # SCQA blueprint JSON (architect)
    draft_blocks: list[str]  # one block per section (stylist/editor)
    lint_report: dict[str, Any]  # serialized LintReport (audit)
    surprisal_report: dict[str, Any]  # serialized surprisal report (audit)
    discourse_report: dict[str, Any]  # StoryScope shape report (audit)
    flagged_blocks: list[int]

    # Loop control
    revision_count: int  # 1 after first draft; +1 per editor pass
    max_revisions: int

    # Outputs
    final_post: str
    scorecard: str
    error: str | None
