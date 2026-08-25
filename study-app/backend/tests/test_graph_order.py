"""Graph structure test — memory must be retrieved BEFORE planning.

The plan node's prompt includes learner-memory personalization instructions
that silently never fired when the order was plan → retrieve_memory.
"""

from __future__ import annotations

from app.agent.graph import GRAPH


def _edges() -> set[tuple[str, str]]:
    return {
        (getattr(e, "source", e[0]), getattr(e, "target", e[1]))
        for e in GRAPH.get_graph().edges
    }


def test_retrieve_memory_precedes_plan():
    edges = _edges()
    assert ("analyze_document", "retrieve_memory") in edges
    assert ("retrieve_memory", "plan") in edges
    assert ("plan", "generate") in edges
    # The old broken edge must be gone.
    assert ("analyze_document", "plan") not in edges
    assert ("plan", "retrieve_memory") not in edges


def test_pipeline_shape_unchanged_otherwise():
    edges = _edges()
    assert ("__start__", "analyze_document") in edges
    assert ("generate", "validate") in edges
    assert ("validate", "finalize") in edges
    assert ("finalize", "__end__") in edges
