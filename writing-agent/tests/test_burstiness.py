from writing_agent.lint.burstiness import check_burstiness

MONOTONE = (
    "The parser reads tokens from the input stream. "
    "The tokenizer splits text into words. "
    "The lexer assigns types to each token. "
    "The parser builds a tree from the tokens. "
    "The evaluator walks the finished tree."
)
HUMAN = (
    "When the indexer collapsed, everything stopped. "
    "We had no runbook (nobody expected the primary to fail during a rolling "
    "upgrade, which in hindsight was optimistic). "
    "Had we chosen Kafka two years earlier the failure mode would have been "
    "different, though maybe not better: the queue would have absorbed the "
    "burst, the consumers would have lagged instead of crashing, and honestly "
    "I still do not know which failure I prefer. "
    "We wrote the postmortem that night."
)


def test_monotone_fails():
    (result,) = check_burstiness([MONOTONE])
    assert result.failed
    assert "SD" in result.reason


def test_human_passes():
    (result,) = check_burstiness([HUMAN])
    assert not result.failed
    assert result.std_dev >= 8.0
    assert result.variance >= 15.0


def test_short_blocks_never_fail():
    results = check_burstiness(
        ["## Setup", "One sentence only.", "Two sentences. Still short."]
    )
    assert all(not r.failed for r in results)
