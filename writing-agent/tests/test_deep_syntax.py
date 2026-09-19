from writing_agent.lint.deep_syntax import check_deep_syntax

FLAT = (
    "The parser reads tokens from the input stream and assigns each one a "
    "type based on the grammar rules. The tokenizer splits text into words "
    "before anything else happens in the pipeline. The lexer then validates "
    "every token against the language specification."
)
DEEP = (
    "When the indexer collapsed, everything stopped. "
    "We had no runbook (nobody expected the primary to fail during a rolling "
    "upgrade, which in hindsight was optimistic). "
    "Had we chosen Kafka two years earlier the failure mode would have been "
    "different, though maybe not better, and honestly I still do not know "
    "which failure I prefer."
)


def test_flat_connective_prose_fails():
    (result,) = check_deep_syntax([FLAT])
    assert result.total == 0
    assert result.failed
    assert "deep-syntax" in result.reason


def test_prose_with_asides_passes():
    (result,) = check_deep_syntax([DEEP])
    assert result.counts["parenthetical"] == 1
    assert result.counts["inversion"] == 1
    assert not result.failed


def test_short_blocks_not_judged():
    results = check_deep_syntax(["## Setup", "Short block."])
    assert results == []
