from writing_agent.scoring.semantic import (
    adjacent_distances,
    axis_value,
    cosine_distance,
    semantic_report,
)

# Distinct unit vectors: [1,0] and [0,1] are orthogonal (distance 1.0).
VEC_A = [1.0, 0.0]
VEC_B = [0.0, 1.0]
VEC_A2 = [0.999, 0.0447]  # ~cos(2.6°) from A — a glide step


def _blocks(n: int) -> list[str]:
    return [f"Section {i} with a couple of sentences. More words follow here." for i in range(n)]


def test_cosine_distance_bounds():
    assert cosine_distance(VEC_A, VEC_A) < 0.001
    assert cosine_distance(VEC_A, VEC_B) > 0.999
    assert cosine_distance([], []) == 1.0


def test_adjacent_distances_length():
    assert len(adjacent_distances([VEC_A, VEC_B, VEC_A])) == 2


def _embed_returning(vectors_by_section: list[list[float]]):
    """Assign consecutive vector pairs to consecutive prose sections so the
    steps follow the intended trajectory."""

    async def fake_embed(texts, *, model=None):
        assert len(texts) <= len(vectors_by_section)
        return vectors_by_section[: len(texts)]

    return fake_embed


async def test_glide_flags_the_block_after_the_flattest_transition():
    # All sections nearly identical in meaning → uniform glide, no dip.
    embed_fn = _embed_returning([VEC_A, VEC_A2, VEC_A, VEC_A2, VEC_A])
    report = await semantic_report(_blocks(5), step_min=0.12, embed_fn=embed_fn)
    assert not report["skipped"]
    assert report["failed"]
    assert report["flagged_blocks"] and report["dip_pair"] is not None
    assert "semantic glide" in report["reason"]
    assert "inject a conceptual shift" in report["reason"]


async def test_jumpy_trajectory_passes():
    embed_fn = _embed_returning([VEC_A, VEC_B, VEC_A, VEC_B, VEC_A])
    report = await semantic_report(_blocks(5), step_min=0.12, embed_fn=embed_fn)
    assert not report["failed"]
    assert report["flagged_blocks"] == []


async def test_few_sections_skip():
    report = await semantic_report(_blocks(1), embed_fn=_embed_returning([VEC_A]))
    assert report["skipped"]


async def test_embed_failure_degrades_gracefully():
    async def boom(texts, *, model=None):
        raise RuntimeError("embeddings unavailable")

    report = await semantic_report(_blocks(3), embed_fn=boom)
    assert report["skipped"]
    assert "skipped" in report["warning"]


def test_axis_value_maps_mean_step():
    assert axis_value({"skipped": False, "step_mean": 0.9}) == 1.0  # saturated
    assert abs(axis_value({"skipped": False, "step_mean": 0.225}) - 0.5) < 0.01
    assert axis_value({"skipped": True, "steps": []}) is None
