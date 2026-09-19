from writing_agent.lint.banned_tokens import load_patterns, scan_banned

SLOP = (
    "Furthermore, it is important to note that this approach is robust. "
    "Additionally, the framework is a testament to good design. "
    "This lets us delve into the details seamlessly."
)
CLEAN = (
    "When the indexer collapsed, everything stopped. We had no runbook. "
    "Had we chosen Kafka two years earlier, the failure mode would have differed."
)


def test_seed_file_loads():
    patterns = load_patterns()
    assert any("delve" in p for p in patterns)
    assert all(not p.startswith("#") for p in patterns)


def test_slop_text_flagged():
    hits = scan_banned([SLOP])
    assert len(hits) == 1
    hit = hits[0]
    assert hit.block_index == 0
    assert hit.count >= 3
    assert "delve" in hit.pattern
    assert "testament to" in hit.pattern


def test_clean_text_passes():
    assert scan_banned([CLEAN]) == []


def test_hits_are_per_block():
    hits = scan_banned([CLEAN, SLOP])
    assert len(hits) == 1
    assert hits[0].block_index == 1
