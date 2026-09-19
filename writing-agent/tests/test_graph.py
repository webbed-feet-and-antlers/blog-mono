from writing_agent.graph import build_graph, route_after_audit
from writing_agent.nodes.audit import audit_node


def _fake_scorer_factory(flag_first: dict):
    async def scorer(blocks, **kw):
        if flag_first.get("on"):
            flag_first["on"] = False
            return {
                "skipped": False,
                "warning": None,
                "blocks": [
                    {
                        "block_index": 0,
                        "failed": True,
                        "reason": "flat surprisal: mean 0.4 bits",
                    }
                ],
                "flagged_blocks": [0],
            }
        return {"skipped": False, "warning": None, "blocks": [], "flagged_blocks": []}

    return scorer


def test_route_finalize_when_clean():
    assert route_after_audit({"flagged_blocks": [], "revision_count": 1}) == "finalize"


def test_route_editor_when_flagged():
    assert route_after_audit({"flagged_blocks": [1], "revision_count": 1}) == "editor"


def test_route_finalize_at_max_revisions():
    assert route_after_audit({"flagged_blocks": [1], "revision_count": 3}) == "finalize"


async def test_audit_merges_lint_and_surprisal_flags():
    state = {
        "draft_blocks": [
            "## H",
            "Furthermore, it is important to note that this approach is "
            "robust and delivers seamless integration for every user. "
            "Additionally, the framework is a testament to good design "
            "across the board. Moreover, teams must consider everything "
            "carefully before adopting anything new here at all.",
        ],
    }
    flag = {"on": True}
    out = await audit_node(state, scorer=_fake_scorer_factory(flag))
    assert set(out["flagged_blocks"]) == {0, 1}  # lint flagged 1, surprisal 0
    assert out["surprisal_report"]["flagged_blocks"] == [0]


async def test_audit_doc_level_transition_failure_flags_prose():
    state = {
        "draft_blocks": [
            "## H",
            "Furthermore, the parser reads tokens from the stream and then "
            "assigns each token a type based on grammar rules in place.",
            "However, the tokenizer splits text into words before anything "
            "else happens in this particular pipeline of ours today.",
            "Moreover, the lexer validates every token against the spec "
            "before the evaluator walks the tree that was just built.",
        ],
    }
    out = await audit_node(state, scorer=_fake_scorer_factory({"on": False}))
    # transition openings across 3 paragraphs → density 1.0 → doc fails;
    # transition blocks 1..3 are flagged so the editor has targets
    assert out["flagged_blocks"] == [1, 2, 3]


async def test_graph_loops_then_finalizes():
    audits = {"n": 0}

    async def architect_stub(state):
        return {"outline": {"scqa": {}, "pillars": []}}

    async def stylist_stub(state):
        return {"draft_blocks": ["Intro.", "Body."], "revision_count": 1}

    async def audit_stub(state):
        audits["n"] += 1
        flagged = [1] if audits["n"] < 3 else []
        return {"flagged_blocks": flagged}

    async def editor_stub(state):
        blocks = list(state["draft_blocks"])
        return {"draft_blocks": blocks, "revision_count": state["revision_count"] + 1}

    def finalize_stub(state):
        return {"final_post": "\n\n".join(state["draft_blocks"]), "scorecard": "done"}

    graph = build_graph(
        architect=architect_stub,
        stylist=stylist_stub,
        audit=audit_stub,
        editor=editor_stub,
        finalize=finalize_stub,
    )
    final = await graph.ainvoke({"topic": "t"})
    assert final["final_post"] == "Intro.\n\nBody."
    assert final["revision_count"] == 3  # draft(1) + two editor passes
    assert audits["n"] == 3
