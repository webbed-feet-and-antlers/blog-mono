"""LangGraph StateGraph — the writing pipeline backbone.

architect → stylist → audit → (editor → audit)* → finalize. The audit's
conditional edge is the "compiler": flagged blocks route to surgical edits,
clean drafts (or exhausted budgets) route to finalize.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from .nodes.architect import architect_node
from .nodes.audit import audit_node
from .nodes.editor import editor_node
from .nodes.finalize import finalize_node
from .nodes.stylist import stylist_node
from .state import WritingState


def _max_revisions() -> int:
    from .config import get_settings

    return get_settings().thresholds.max_revisions


def route_after_audit(state: WritingState) -> str:
    if not state.get("flagged_blocks"):
        return "finalize"
    if state.get("revision_count", 1) >= _max_revisions():
        return "finalize"
    return "editor"


def build_graph(
    *,
    architect=architect_node,
    stylist=stylist_node,
    audit=audit_node,
    editor=editor_node,
    finalize=finalize_node,
):
    builder = StateGraph(WritingState)
    builder.add_node("architect", architect)
    builder.add_node("stylist", stylist)
    builder.add_node("audit", audit)
    builder.add_node("editor", editor)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "architect")
    builder.add_edge("architect", "stylist")
    builder.add_edge("stylist", "audit")
    builder.add_conditional_edges(
        "audit", route_after_audit, {"editor": "editor", "finalize": "finalize"}
    )
    builder.add_edge("editor", "audit")
    builder.add_edge("finalize", END)
    return builder.compile()


GRAPH = build_graph()


async def run_pipeline(
    *,
    topic: str,
    audience: str = "technical practitioners",
    persona: str = "",
    raw_research: str = "",
    style_reference_paths: list[str] | None = None,
) -> dict[str, Any]:
    initial: WritingState = {
        "topic": topic,
        "audience": audience,
        "persona": persona,
        "raw_research": raw_research,
        "style_reference_paths": style_reference_paths or [],
    }
    return await GRAPH.ainvoke(initial)
