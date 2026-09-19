from writing_agent.lint.redundancy import check_redundancy
from writing_agent.segment import split_blocks

SECTION_A = (
    "When the indexer collapsed, everything stopped. "
    "We had no runbook (nobody expected the primary to fail during a rolling "
    "upgrade, which in hindsight was optimistic). "
    "Had we chosen Kafka two years earlier the failure mode would have been "
    "different, though maybe not better, and I still do not know which "
    "failure I prefer. The postmortem ran long."
)
# Repeats A nearly verbatim — the re-explaining block.
SECTION_B = (
    "When the indexer collapsed, everything stopped. "
    "We had no runbook (nobody expected the primary to fail during a rolling "
    "upgrade, which in hindsight was optimistic). "
    "Had we chosen Kafka two years earlier the failure mode would have been "
    "different, though maybe not better, and I still do not know which "
    "failure I prefer. The postmortem ran long. The queue absorbed nothing."
)
SECTION_DISTINCT = (
    "Three of us traced the retries to a 2019 commit. The fix was four "
    "lines (the test for it was forty, which taught me something about "
    "confidence). We shipped it on a Tuesday. Nobody cheered. "
    "Everybody should have."
)


def test_redundant_pair_flagged():
    result = check_redundancy([SECTION_A, SECTION_B])
    assert result.failed
    (pair,) = result.pairs
    assert pair.block_a == 0 and pair.block_b == 1
    assert pair.jaccard > 0.5
    assert pair.shared  # evidence grams present


def test_distinct_sections_pass():
    result = check_redundancy([SECTION_A, SECTION_DISTINCT])
    assert not result.failed
    assert result.pairs == []


def test_short_blocks_ignored():
    result = check_redundancy(["Tiny block one here.", "Tiny block one here."])
    assert not result.failed  # below min_words — no comparison


def test_headings_and_code_ignored():
    blocks = split_blocks("## H\n\n" + SECTION_A + "\n\n```python\nx = 1\n```\n\n" + SECTION_B)
    result = check_redundancy(blocks)
    assert result.failed
    (pair,) = result.pairs
    assert pair.block_a == 1 and pair.block_b == 3  # prose blocks only
