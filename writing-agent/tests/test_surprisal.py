import math

from writing_agent.scoring.surprisal import (
    _halves,
    calibrate,
    score_blocks,
    stats_from_logprobs,
    token_overlap,
)

# 4+ sentences so the block splits into halves; the "slop" second half is
# generic enough for a blind model to reproduce.
SLOP_BLOCK = (
    "Our platform delivers value across the organization. "
    "The system provides a robust foundation for future growth. "
    "Teams can leverage the framework to unlock new potential. "
    "This approach ensures seamless integration for every user. "
    "The results speak for themselves across the industry."
)
# Distinctive, specific second half — a blind model can't reproduce it.
HUMAN_BLOCK = (
    "The indexer fell over at 2am on a Saturday. "
    "We had no runbook (nobody expected the primary to fail during a rolling "
    "upgrade, which in hindsight was optimistic). "
    "Had we chosen Kafka two years earlier the failure mode would have "
    "differed, though maybe not better. "
    "The queue would have absorbed the burst and the consumers would have "
    "lagged horribly instead of crashing outright. "
    "I still do not know which failure I prefer."
)


def _llm_returning(text: str, logprobs: list[float] | None):
    async def fake_llm(prefix, *, model=None, max_tokens=300):
        # score_blocks passes the RAW document prefix; the <document> wrapper
        # lives inside the real chat_score helper, not here.
        return text, logprobs if logprobs is not None else []

    return fake_llm


def test_stats_from_logprobs_bits():
    flat = stats_from_logprobs([-0.25, -0.25, -0.25, -0.25])
    assert flat["n_tokens"] == 4
    assert abs(flat["mean_bits"] - (0.25 / math.log(2))) < 1e-9
    assert flat["variance_bits"] == 0.0
    assert flat["peak_count"] == 0


def test_token_overlap():
    overlap = token_overlap(
        "teams can leverage the framework to unlock new potential today",
        "Teams can leverage the framework to unlock new potential.",
    )
    assert overlap > 0.9
    assert token_overlap("kafka lagged consumers crashed", SLOP_BLOCK) < 0.2


def test_halves_requires_three_sentences():
    assert _halves("One sentence. Two sentences.") is None
    halves = _halves("A one. B two. C three words. D four more words.")
    assert halves is not None
    assert halves[0] == "A one. B two."
    assert halves[1] == "C three words. D four more words."


async def test_slop_block_flagged_when_blind_model_reproduces_it():
    # Simulate a scorer that confidently regurgitates the generic second half.
    slop_half = _halves(SLOP_BLOCK)[1]
    llm = _llm_returning(slop_half, [-0.2] * 30)  # ≈0.29 bits, very confident
    report = await score_blocks([SLOP_BLOCK], llm=llm)
    assert not report["skipped"]
    assert report["flagged_blocks"] == [0]
    assert "predictability" in report["blocks"][0]["reason"]


async def test_distinctive_block_passes_when_model_hesitates():
    # A blind model that continues uncertainly (high surprisal) is the
    # human-text signature: nothing predictable to ride.
    llm = _llm_returning(
        "The platform enables synergy across verticals for stakeholders.",
        [-2.0] * 20,  # ≈2.9 bits, hesitant
    )
    report = await score_blocks([HUMAN_BLOCK], llm=llm)
    assert report["flagged_blocks"] == []


async def test_confident_continuation_flags_even_when_words_diverge():
    # Primary signal is surprisal, not verbatim overlap: a model continuing
    # this context confidently (0.29 bits) means the register is predictable.
    llm = _llm_returning(
        "The platform enables synergy across verticals for stakeholders.",
        [-0.2] * 20,
    )
    report = await score_blocks([HUMAN_BLOCK], llm=llm)
    assert report["flagged_blocks"] == [0]


async def test_overlap_only_when_logprobs_unavailable():
    slop_half = _halves(SLOP_BLOCK)[1]
    llm = _llm_returning(slop_half, None)  # provider omitted logprobs
    report = await score_blocks([SLOP_BLOCK], llm=llm)
    assert report["blocks"][0]["mean_bits"] is None
    assert report["flagged_blocks"] == [0]


async def test_scorer_failure_degrades_gracefully():
    async def boom(prefix, **kw):
        raise RuntimeError("no scoring support")

    report = await score_blocks(
        ["First sentence here. Second sentence follows. Third one now."],
        llm=boom,
    )
    assert report["skipped"] is True
    assert "scoring skipped" in report["warning"]
    assert report["flagged_blocks"] == []


async def test_code_heading_and_short_blocks_skipped():
    llm = _llm_returning("whatever", [-0.2] * 5)
    report = await score_blocks(
        ["## Heading", "```python\nx = 1\n```", "Short."], llm=llm
    )
    assert report["blocks"] == []


async def test_calibrate_suggests_overlap_gate(tmp_path):
    llm = _llm_returning("totally different words than the human wrote", None)
    f = tmp_path / "ref.md"
    f.write_text(HUMAN_BLOCK)
    out = await calibrate([f], llm=llm)
    assert out["human_overlap_p90"] is not None
    assert out["suggested_overlap_max"] >= out["human_overlap_p90"]
