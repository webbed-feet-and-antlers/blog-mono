"""LangGraph StateGraph — the shared agent backbone.

All three features run through this one graph. The only feature-specific
branching is inside the `generate` node, which dispatches by task_type.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import AgentState, TaskType

logger = logging.getLogger(__name__)

# Build the graph once. It's stateless — per-run state is passed in.
_graph_builder = StateGraph(AgentState)
_graph_builder.add_node("analyze_document", nodes.analyze_document)
_graph_builder.add_node("plan", nodes.plan)
_graph_builder.add_node("retrieve_memory", nodes.retrieve_memory)
_graph_builder.add_node("generate", nodes.generate)
_graph_builder.add_node("validate", nodes.validate)
_graph_builder.add_node("finalize", nodes.finalize)

_graph_builder.add_edge(START, "analyze_document")
_graph_builder.add_edge("analyze_document", "plan")
_graph_builder.add_edge("plan", "retrieve_memory")
_graph_builder.add_edge("retrieve_memory", "generate")
_graph_builder.add_edge("generate", "validate")
# On validation failure we stop (don't persist bad output). A future iteration
# could route back to `generate` for a retry with feedback.
_graph_builder.add_conditional_edges(
    "validate",
    lambda state: "finalize"
    if state.get("validation", {}).get("ok")
    else END,
    {"finalize": "finalize", END: END},
)
_graph_builder.add_edge("finalize", END)

GRAPH = _graph_builder.compile()


def build_graph():
    """Return the compiled graph (kept for external callers/tests)."""
    return GRAPH


async def run_generation(
    *,
    document_id: str,
    document_text: str,
    task_type: TaskType,
    session: Any,
    instructions: str | None = None,
) -> dict[str, Any]:
    """Run the full agent pipeline for one generation request.

    Returns the final state, which includes `content_item` (the persisted
    payload) or `error`.
    """
    initial: AgentState = {
        "document_id": document_id,
        "document_text": document_text,
        "task_type": task_type,
        "instructions": instructions,
        "session": session,
        "messages": [],
        "error": None,
    }
    final_state = await GRAPH.ainvoke(initial)
    return final_state
