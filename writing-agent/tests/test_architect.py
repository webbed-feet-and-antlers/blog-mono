import pytest

from writing_agent.nodes.architect import (
    architect_node,
    normalize_outline,
    validate_outline,
)

GOOD = {
    "title": "Why our indexer collapsed",
    "style_notes": ["one-line paragraphs after code"],
    "scqa": {
        "situation": "s",
        "complication": "c",
        "question": "q",
        "answer": "a",
    },
    "pillars": [
        {"heading": "H1", "claim": "c1", "evidence": ["e"], "stylistic_mode": "anecdote-led"},
        {"heading": "H2", "claim": "c2", "evidence": ["e"], "stylistic_mode": "dense-analytical"},
    ],
    "open_loops": ["queue lag vs crash"],
}


def test_validate_outline_accepts_good():
    validate_outline(GOOD)  # no raise


def test_validate_outline_rejects_missing_scqa():
    bad = {**GOOD, "scqa": {"situation": "s"}}
    with pytest.raises(ValueError, match="scqa"):
        validate_outline(bad)


def test_validate_outline_rejects_too_few_pillars():
    bad = {**GOOD, "pillars": [GOOD["pillars"][0]]}
    with pytest.raises(ValueError, match="pillars"):
        validate_outline(bad)


def test_normalize_outline_maps_aliases():
    # Real drift observed from claude-sonnet-5: pillars keyed 'name',
    # no 'claim'; scqa keyed 'problem'/'thesis'.
    drifted = {
        "title": "T",
        "scqa": {
            "context": "s",
            "problem": "c",
            "question": "q",
            "thesis": "a",
        },
        "pillars": [
            {"name": "Buttons, not prompts", "evidence": ["e"], "stylistic_mode": "anecdote-led"},
            {"name": "The mirror problem", "claim": "c2", "evidence": ["e2"], "stylistic_mode": "counter-argument"},
        ],
        "open_loops": [],
    }
    fixed = normalize_outline(drifted)
    assert fixed["scqa"]["situation"] == "s"
    assert fixed["scqa"]["complication"] == "c"
    assert fixed["scqa"]["answer"] == "a"
    assert fixed["pillars"][0]["heading"] == "Buttons, not prompts"
    assert fixed["pillars"][0]["claim"] == "Buttons, not prompts"
    validate_outline(fixed)  # no raise after normalization


async def test_architect_node_returns_validated_outline(monkeypatch, tmp_path):
    ref = tmp_path / "ref.md"
    ref.write_text("# Some human post\n\nShort. Punchy (with asides).")

    async def fake_llm(messages, **kw):
        assert kw["temperature"] == 0.2
        return GOOD

    state = {
        "topic": "indexer outage",
        "audience": "backend engineers",
        "persona": "casual",
        "raw_research": "notes",
        "style_reference_paths": [str(ref)],
    }
    out = await architect_node(state, llm=fake_llm)
    assert out["outline"]["title"] == "Why our indexer collapsed"
    assert len(out["outline"]["pillars"]) == 2


async def test_architect_node_repairs_invalid_blueprint():
    calls = []

    async def flaky_llm(messages, **kw):
        calls.append(messages)
        if len(calls) == 1:
            return {"scqa": GOOD["scqa"], "pillars": [GOOD["pillars"][0]]}  # 1 pillar
        return GOOD

    out = await architect_node({"topic": "t"}, llm=flaky_llm)
    assert len(calls) == 2  # original + one repair pass
    assert "invalid" in calls[1][-1]["content"].lower()
    assert len(out["outline"]["pillars"]) == 2
