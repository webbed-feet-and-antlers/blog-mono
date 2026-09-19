from writing_agent.nodes.editor import editor_node, reasons_by_block


def _state(flagged=(1,), lint_reasons=None, surprisal_reasons=None):
    return {
        "draft_blocks": ["Intro.", "Bad block with delve.", "Fine block."],
        "flagged_blocks": list(flagged),
        "revision_count": 1,
        "lint_report": {
            "failures": [
                {
                    "block_index": 1,
                    "reasons": lint_reasons or ["banned n-grams (1): delve"],
                },
            ],
        },
        "surprisal_report": {
            "blocks": [
                {
                    "block_index": 1,
                    "failed": bool(surprisal_reasons),
                    "reason": surprisal_reasons,
                },
            ],
        },
    }


def test_reasons_by_block_merges_sources():
    merged = reasons_by_block(
        _state(surprisal_reasons="flat surprisal: mean 0.4")
    )
    assert "delve" in merged[1]
    assert "flat surprisal" in merged[1]


async def test_editor_rewrites_only_flagged_blocks():
    calls = []

    async def fake_llm(messages, **kw):
        calls.append((messages, kw))
        return "Fixed. (With an aside, for good measure.)"

    out = await editor_node(_state(), llm=fake_llm)
    assert out["draft_blocks"][0] == "Intro."  # untouched
    assert out["draft_blocks"][2] == "Fine block."  # untouched
    assert out["draft_blocks"][1].startswith("Fixed.")
    assert out["revision_count"] == 2
    assert len(calls) == 1  # one call, for the one flagged block
    assert calls[0][1]["temperature"] == 0.15
    assert "delve" in calls[0][0][1]["content"]  # defect reasons in prompt
