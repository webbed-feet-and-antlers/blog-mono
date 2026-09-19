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
    async def fake_llm(prefix, *, model=None, max_tokens=300, temperature=0.0):
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
    slop_half = _halves(SLOP_BLOCK)[1]
    llm = _llm_returning(slop_half, [-0.2] * 30)
    report = await score_blocks([SLOP_BLOCK], llm=llm, samples=0)
    assert not report["skipped"]
    assert report["flagged_blocks"] == [0]
    assert "predictability" in report["blocks"][0]["reason"]


async def test_distinctive_block_passes_when_model_hesitates():
    llm = _llm_returning(
        "The platform enables synergy across verticals for stakeholders.",
        [-2.0] * 20,  # ≈2.9 bits, hesitant
    )
    report = await score_blocks([HUMAN_BLOCK], llm=llm, samples=0)
    assert report["flagged_blocks"] == []


async def test_confident_continuation_flags_even_when_words_diverge():
    llm = _llm_returning(
        "The platform enables synergy across verticals for stakeholders.",
        [-0.2] * 20,
    )
    report = await score_blocks([HUMAN_BLOCK], llm=llm, samples=0)
    assert report["flagged_blocks"] == [0]


async def test_overlap_only_when_logprobs_unavailable():
    slop_half = _halves(SLOP_BLOCK)[1]
    llm = _llm_returning(slop_half, None)
    report = await score_blocks([SLOP_BLOCK], llm=llm, samples=0)
    assert report["blocks"][0]["mean_bits"] is None
    assert report["flagged_blocks"] == [0]


async def test_scorer_failure_degrades_gracefully():
    async def boom(prefix, **kw):
        raise RuntimeError("no scoring support")

    report = await score_blocks(
        ["First sentence here. Second sentence follows. Third one now."],
        llm=boom,
        samples=0,
    )
    assert report["skipped"] is True
    assert "scoring skipped" in report["warning"]
    assert report["flagged_blocks"] == []


async def test_code_and_heading_blocks_skipped():
    llm = _llm_returning("whatever", [-0.2] * 5)
    report = await score_blocks(
        ["## Heading", "```python\nx = 1\n```", "Short."], llm=llm, samples=0
    )
    assert report["blocks"] == []


async def test_calibrate_suggests_overlap_gate(tmp_path):
    llm = _llm_returning("totally different words than the human wrote", None)
    f = tmp_path / "ref.md"
    f.write_text(HUMAN_BLOCK)
    out = await calibrate([f], llm=llm)
    assert out["human_overlap_p90"] is not None
    assert out["suggested_overlap_max"] >= out["human_overlap_p90"]


# ---- doc-level predictability burstiness (GPTZero-style) ----


def _blocks(n: int) -> list[str]:
    return [
        f"First sentence for block {i} here now. Second sentence follows "
        f"the first one. Third sentence closes block {i} out."
        for i in range(n)
    ]


def _bits_llm(bits_by_call: list[float]):
    calls = {"n": 0}

    async def fake_llm(prefix, *, model=None, max_tokens=300, temperature=0.0):
        bits = bits_by_call[calls["n"] % len(bits_by_call)]
        calls["n"] += 1
        return "whatever continuation", [-bits * math.log(2)] * 12

    return fake_llm


async def test_uniform_bits_doc_flagged_with_flattest_block():
    report = await score_blocks(
        _blocks(5), llm=_bits_llm([0.5, 0.52, 0.48, 0.51, 0.49]), samples=0
    )
    burst = report["bits_burstiness"]
    assert burst is not None and burst["failed"]
    assert burst["bits_sd"] < 0.15
    assert 2 in report["flagged_blocks"]  # the flattest block (0.48)
    flattest = next(b for b in report["blocks"] if b["block_index"] == 2)
    assert "uniformly predictable" in flattest["reason"]


async def test_varied_bits_doc_not_doc_flagged():
    # All blocks above the per-block gate (default bits_max=1.2), but with
    # enough spread across blocks to clear the doc-level SD gate.
    report = await score_blocks(
        _blocks(5), llm=_bits_llm([1.3, 1.6, 1.3, 2.0, 1.3]), samples=0
    )
    assert report["bits_burstiness"]["failed"] is False
    assert report["flagged_blocks"] == []


async def test_few_blocks_skip_burstiness_gate():
    report = await score_blocks(_blocks(2), llm=_bits_llm([0.5, 0.5]), samples=0)
    assert report["bits_burstiness"] is None


# ---- multi-sample convergence (Fast-DetectGPT curvature, adapted) ----


def _converging_llm(sample_text: str):
    """Greedy call returns hesitant bits (passes the bits gate); sampled
    calls (temperature > 0.1) return `sample_text` — identical when the
    context converges, distinct texts otherwise."""

    async def fake_llm(prefix, *, model=None, max_tokens=300, temperature=0.0):
        if temperature > 0.1:
            return sample_text, []
        return "greedy continuation", [-1.5] * 10  # ≈2.2 bits, passes

    return fake_llm


def _diverging_llm():
    async def fake_llm(prefix, *, model=None, max_tokens=300, temperature=0.0):
        if temperature > 0.1:
            fake_llm.n += 1
            variants = [
                "kafka absorbed the burst that saturday night",
                "the connection pool closed politely again",
                "three engineers traced a 2019 commit instead",
            ]
            return variants[fake_llm.n % 3], []
        return "greedy continuation", [-1.5] * 10

    fake_llm.n = -1
    return fake_llm


async def test_convergent_samples_flag_block():
    llm = _converging_llm("the platform delivers seamless value across the organization")
    report = await score_blocks([SLOP_BLOCK], llm=llm, samples=3)
    block = report["blocks"][0]
    assert block["agreement"] >= 0.5
    assert report["flagged_blocks"] == [0]
    assert "convergent continuations" in block["reason"]


async def test_diverging_samples_pass():
    report = await score_blocks([HUMAN_BLOCK], llm=_diverging_llm(), samples=3)
    block = report["blocks"][0]
    assert block["agreement"] < 0.5
    assert report["flagged_blocks"] == []


async def test_sample_failures_leave_agreement_none():
    async def flaky_llm(prefix, *, model=None, max_tokens=300, temperature=0.0):
        if temperature > 0.1:
            raise RuntimeError("sampling unavailable")
        return "greedy continuation", [-1.5] * 10

    report = await score_blocks([SLOP_BLOCK], llm=flaky_llm, samples=3)
    block = report["blocks"][0]
    assert block["agreement"] is None  # gate skipped, greedy signal intact
    assert report["flagged_blocks"] == []


# ---- observer corroboration (Binoculars-style second family) ----


def _observer_llm(greedy_text, observer_text, sample_texts, greedy_bits=2.2):
    calls = {"n": 0}

    async def fake_llm(
        prefix, *, model=None, max_tokens=300, temperature=0.0, logprobs=True
    ):
        if model == "fake/observer":
            return observer_text, []
        if temperature > 0.1:
            out = sample_texts[calls["n"] % len(sample_texts)]
            calls["n"] += 1
            return out, []
        return greedy_text, [-greedy_bits * math.log(2)] * 10

    return fake_llm


async def test_observer_corroboration_upgrades_bits_flag_reason():
    greedy = "the platform delivers seamless value across the organization"
    llm = _observer_llm(greedy, greedy, ["unrelated sample words", "other words"], greedy_bits=0.5)
    report = await score_blocks(
        [SLOP_BLOCK],
        llm=llm,
        samples=2,
        observer_model="fake/observer",
    )
    block = report["blocks"][0]
    assert block["corroboration"] >= 0.5
    assert block["failed"]  # via bits (0.5 <= 1.2)
    assert "cross-model convergence" in block["reason"]


async def test_observer_plus_converging_samples_flags_clean_bits():
    greedy = "the platform delivers seamless value across the organization"
    llm = _observer_llm(
        greedy,
        greedy,
        [greedy, greedy],  # samples also converge
        greedy_bits=2.2,  # bits gate passes
    )
    report = await score_blocks(
        [SLOP_BLOCK], llm=llm, samples=2, observer_model="fake/observer"
    )
    assert report["flagged_blocks"] == [0]
    assert "cross-model convergence" in report["blocks"][0]["reason"]


async def test_observer_failure_leaves_corroboration_none():
    calls = {"n": 0}

    async def flaky_llm(
        prefix, *, model=None, max_tokens=300, temperature=0.0, logprobs=True
    ):
        if model == "fake/observer":
            raise RuntimeError("observer down")
        if temperature > 0.1:
            calls["n"] += 1
            variants = [
                "kafka absorbed the burst that night",
                "engineers traced a 2019 commit instead",
            ]
            return variants[(calls["n"] - 1) % 2], []
        return "greedy continuation", [-2.2 * math.log(2)] * 10

    report = await score_blocks(
        [SLOP_BLOCK], llm=flaky_llm, samples=2, observer_model="fake/observer"
    )
    assert report["blocks"][0]["corroboration"] is None
    assert report["flagged_blocks"] == []
