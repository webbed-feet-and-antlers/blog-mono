from writing_agent.lint.transitions import check_transitions

TRANSITIONY = [
    "Furthermore, the parser reads tokens from the stream and assigns types.",
    "The tokenizer splits text into words before anything else happens here.",
    "However, the lexer needs a fallback for unknown characters in input.",
]
VARIED = [
    "The parser reads tokens from the stream. It works.",
    "Tokenization splits text first. Then typing happens.",
    "Unknown characters break the lexer. We patched it.",
]
EM_DASHY = (
    "The system — which we built — handles retries — badly — and the on-call "
    "rotation — three people — felt every one of those nights — painfully."
)


def test_transition_openings_fail():
    result = check_transitions(TRANSITIONY)
    assert result.doc_density == 2 / 3  # Furthermore + However, not "The"
    assert result.failed
    assert result.transition_block_indices == [0, 2]


def test_varied_openings_pass():
    result = check_transitions(VARIED)
    assert result.doc_density == 0.0
    assert not result.failed


def test_em_dash_density_flagged():
    result = check_transitions([EM_DASHY])
    assert result.em_dash_per_1k[0] > 6.0
    assert result.failed
    assert "em-dash" in result.reason


def test_in_addition_phrase_counts():
    result = check_transitions(["In addition, the parser reads the tokens."])
    assert result.doc_density == 1.0
    assert result.failed


def test_headings_and_code_ignored():
    result = check_transitions(["## Furthermore", "```python\nx = 1\n```"])
    assert result.doc_density == 0.0
    assert not result.failed
