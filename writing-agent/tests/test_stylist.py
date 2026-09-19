import re

from writing_agent.nodes.stylist import stylist_node

OUTLINE = {
    "title": "Why our indexer collapsed",
    "style_notes": ["one-line paragraphs after code"],
    "scqa": {
        "situation": "We ran a 3-node indexer.",
        "complication": "It collapsed during a rolling upgrade.",
        "question": "Why did the fallback not catch it?",
        "answer": "The queue was absorbing the wrong failure.",
    },
    "pillars": [
        {"heading": "The night it broke", "claim": "c1", "evidence": ["e1"], "stylistic_mode": "anecdote-led"},
        {"heading": "The queue lie", "claim": "c2", "evidence": ["e2"], "stylistic_mode": "dense-analytical"},
        {"heading": "What we kept", "claim": "c3", "evidence": ["e3"], "stylistic_mode": "anecdote-led"},
    ],
    "open_loops": ["lag vs crash still unresolved"],
}


def _llm_recording(sink):
    async def fake_llm(messages, **kw):
        sink.append((messages, kw))
        m = re.search(r'Write the pillar section "(.*?)"', messages[1]["content"])
        if m:
            return (
                f"## {m.group(1)}\n\n"
                "Short punch. Then a much longer sentence that carries the "
                "real technical payload of the section (which in hindsight "
                "was optimistic). Done."
            )
        return "Short punch. Longer sentence with the payload here (an aside). Done."

    return fake_llm


async def test_stylist_produces_block_per_pillar_plus_intro_and_outro():
    sink = []
    out = await stylist_node(
        {"outline": OUTLINE, "persona": "casual"}, llm=_llm_recording(sink)
    )
    blocks = out["draft_blocks"]
    assert len(blocks) == 5  # intro + 3 pillars + outro
    assert blocks[1].startswith("## The night it broke")
    assert out["revision_count"] == 1


async def test_stylist_prompt_carries_constraints_and_sampling():
    sink = []
    await stylist_node(
        {"outline": OUTLINE, "persona": "be messy"}, llm=_llm_recording(sink)
    )
    user_prompt = sink[0][0][1]["content"]
    assert "candidate opener" in user_prompt  # verbalized sampling
    assert "parenthetical" in user_prompt  # deep-syntax enforcer
    assert "Banned words (hard)" in user_prompt  # negative constraints
    assert "be messy" in user_prompt  # persona injected
    assert sink[0][1]["temperature"] == 0.9  # high-entropy drafting
    assert sink[0][1]["top_p"] == 0.92


async def test_modes_rotate_when_architect_repeats():
    sink = []
    await stylist_node({"outline": OUTLINE}, llm=_llm_recording(sink))
    # pillar prompts are calls 1..3 (call 0 is the intro)
    modes = [
        sink[i][0][1]["content"].split("Mode: ")[1].split("\n")[0]
        for i in range(1, 4)
    ]
    assert modes[0] != modes[1]  # adjacent pillars never share a mode
    assert modes[1] != modes[2]
