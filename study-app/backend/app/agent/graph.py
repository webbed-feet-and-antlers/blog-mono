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


# Maps each agent node to a friendly, human-readable status label for the UI.
# These are deliberately vague/pleasant — they say what stage the agent is at
# without exposing implementation details or trace logs.
NODE_STATUSES: dict[str, str] = {
    "analyze_document": "Reading the document…",
    "plan": "Planning what to create…",
    "retrieve_memory": "Recalling what you know…",
    "generate": "Creating your {task_type}…",
    "validate": "Checking the quality…",
    "finalize": "Saving the results…",
}


def _friendly_status(node: str, task_type: str) -> str:
    """Return a user-facing status string for a node name."""
    template = NODE_STATUSES.get(node, "Working…")
    if "{task_type}" in template:
        # task_type is notes/quiz/flashcards — use the singular noun.
        noun = task_type.rstrip("s") if task_type.endswith("s") else task_type
        return template.format(task_type=noun)
    return template


async def run_generation_streamed(
    *,
    document_id: str,
    document_text: str,
    task_type: TaskType,
    session: Any,
    instructions: str | None = None,
):
    """Run the agent pipeline, yielding (status_str, state_dict) tuples as
    each node completes.

    The final yield has status "done" and the full final state. If the agent
    errors, the status is "error" and the state carries the error message.

    Usage:
        async for status, state in run_generation_streamed(...):
            # status is a friendly string; state is the latest AgentState
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

    # Emit the very first status before any node runs.
    yield _friendly_status("analyze_document", task_type), initial

    final_state = initial
    try:
        async for chunk in GRAPH.astream(initial, stream_mode="updates"):
            # LangGraph "updates" mode yields {node_name: state_delta} per step.
            for node_name in chunk:
                final_state = {**final_state, **chunk[node_name]}
                yield _friendly_status(node_name, task_type), final_state
    except Exception as exc:
        logger.exception("Agent streaming generation failed")
        final_state["error"] = str(exc)
        yield "error", final_state
        return

    yield "done", final_state
