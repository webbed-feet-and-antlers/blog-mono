from writing_agent.nodes.editor import editor_node, reasons_by_block

COLD = "Fixed. (With an aside, for good measure.)"
WARM = "Fixed shorter. (An aside.) Then one longer sentence carrying the real payload of the block."


def _state(flagged=(1,)):
    return {
        "draft_blocks": ["Intro.", "Bad block with delve.", "Fine block."],
        "flagged_blocks": list(flagged),
        "revision_count": 1,
        "lint_report": {
            "failures": [
                {
                    "block_index": 1,
                    "reasons": ["banned n-grams (1): delve"],
                },
            ],
        },
        "surprisal_report": {"blocks": []},
    }


def _llm_two_candidates():
    """Dispatch on temperature: cold (<0.3) → COLD, warm (≥0.3) → WARM."""

    async def fake_llm(messages, **kw):
        return COLD if kw.get("temperature", 0.0) < 0.3 else WARM

    return fake_llm


def _judge_picking(choice):
    async def fake_judge(messages, **kw):
        assert "CANDIDATE 1" in messages[1]["content"]
        assert "CANDIDATE 2" in messages[1]["content"]
        return {"choice": choice, "reason": "punchier"}

    return fake_judge


def test_reasons_by_block_merges_sources():
    merged = reasons_by_block(
        _state_with_surprisal()
    )
    assert "delve" in merged[1]
    assert "flat surprisal" in merged[1]


def _state_with_surprisal():
    state = _state()
    state["surprisal_report"] = {
        "blocks": [
            {
                "block_index": 1,
                "failed": True,
                "reason": "flat surprisal: mean 0.4",
            },
        ],
    }
    return state


async def test_pairwise_judge_choice_1_wins_presented_first():
    # Flagged block index 1 → cold is presented as CANDIDATE 2 (swap on),
    # so choice 1 selects the WARM candidate.
    out = await editor_node(
        _state(), llm=_llm_two_candidates(), judge=_judge_picking(1)
    )
    assert out["draft_blocks"][1] == WARM
    assert out["draft_blocks"][0] == "Intro."  # untouched
    assert out["revision_count"] == 2


async def test_pairwise_judge_choice_2_selects_cold_under_swap():
    out = await editor_node(
        _state(), llm=_llm_two_candidates(), judge=_judge_picking(2)
    )
    assert out["draft_blocks"][1] == COLD


async def test_pairwise_invalid_judge_falls_back_to_cold():
    out = await editor_node(
        _state(), llm=_llm_two_candidates(), judge=_judge_picking(7)
    )
    assert out["draft_blocks"][1] == COLD


async def test_pairwise_judge_exception_falls_back_to_cold():
    async def boom(messages, **kw):
        raise RuntimeError("judge down")

    out = await editor_node(_state(), llm=_llm_two_candidates(), judge=boom)
    assert out["draft_blocks"][1] == COLD


async def test_pairwise_disabled_uses_single_cold_candidate():
    calls = []

    async def counting_llm(messages, **kw):
        calls.append(kw.get("temperature"))
        return COLD

    async def should_not_run(messages, **kw):
        raise AssertionError("judge must not be called when pairwise is off")

    out = await editor_node(
        _state(), llm=counting_llm, judge=should_not_run, pairwise=False
    )
    assert out["draft_blocks"][1] == COLD
    assert calls == [0.15]  # one call, cold temperature only
