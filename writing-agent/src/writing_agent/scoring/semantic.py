"""Semantic embedding divergence — arXiv:2609.07920 ("humans introduce,
models elaborate").

Humans introduce semantic SHIFTS between sections; models elaborate the
previous section, so their turn-to-turn embedding distance stays uniformly
small. The machine signature is a flat glide with no dips: we embed each
prose section, measure cosine distance between adjacent sections, and gate
on the MINIMUM step — a document that never dips below `semantic_step_min`
never changed direction. When the gate trips, the block after the flattest
transition gets flagged ("inject a conceptual shift"), and we embed that
block's sentences too, to locate the flat stretch precisely.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from ..llm import embed
from ..segment import is_code_block, is_heading_block, split_sentences

logger = logging.getLogger(__name__)

AXIS_CAP = 0.45  # radar-axis saturation: mean adjacent distance (~human max)


def cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if not na or not nb:
        return 1.0
    return 1.0 - dot / (na * nb)


def adjacent_distances(vectors: list[list[float]]) -> list[float]:
    return [cosine_distance(a, b) for a, b in zip(vectors, vectors[1:])]


def axis_value(report: dict[str, Any], cap: float = AXIS_CAP) -> float | None:
    """Normalized radar-axis value: outward = jumpy/human."""
    if report.get("skipped") or report.get("step_mean") is None:
        return None
    return max(0.0, min(1.0, report["step_mean"] / cap))


async def semantic_report(
    blocks: list[str],
    *,
    step_min: float = 0.12,
    embed_fn=embed,
) -> dict[str, Any]:
    prose = [
        (i, b)
        for i, b in enumerate(blocks)
        if not is_code_block(b) and not is_heading_block(b)
    ]
    if len(prose) < 2:
        return {"skipped": True, "warning": "fewer than two prose sections", "steps": []}
    try:
        vectors = await embed_fn([b for _, b in prose])
    except Exception as exc:
        logger.warning("semantic gate skipped: %r", exc)
        return {
            "skipped": True,
            "warning": f"semantic scoring skipped: {exc}",
            "steps": [],
        }

    steps = adjacent_distances(vectors)
    step_min_observed = min(steps) if steps else None
    step_mean = sum(steps) / len(steps) if steps else None
    step_sd = (
        (sum((s - step_mean) ** 2 for s in steps) / len(steps)) ** 0.5
        if steps
        else None
    )
    # Machine signature: a flat glide — NO adjacent pair is distant. The
    # gate fails when the LARGEST step never reaches the minimum jump.
    step_max = max(steps) if steps else None
    failed = bool(steps) and step_max < step_min
    dip_pair: list[int] | None = None
    reason = None
    flagged_blocks: list[int] = []
    detail: dict[str, Any] | None = None
    if failed:
        flattest = steps.index(step_min_observed)  # most glide-y transition
        dip_pair = [prose[flattest][0], prose[flattest + 1][0]]
        flagged_blocks = [dip_pair[1]]
        reason = (
            f"semantic glide: the largest shift between adjacent sections "
            f"is only {step_max:.2f} (needs at least one ≥ {step_min}) — "
            f"sections {dip_pair[0]}→{dip_pair[1]} flow smoothly into each "
            f"other; inject a conceptual shift (a counter-example, a "
            f"failure, a jump in time)"
        )
        # Sentence-level escalation inside the flattest transition's target:
        # embed its sentences to locate the flat stretch precisely.
        target_sentences = [s for s in split_sentences(prose[flattest + 1][1]) if s.strip()]
        if len(target_sentences) >= 3:
            try:
                s_vecs = await embed_fn(target_sentences)
                s_steps = adjacent_distances(s_vecs)
                detail = {
                    "block_index": dip_pair[1],
                    "n_sentences": len(target_sentences),
                    "sentence_step_min": min(s_steps),
                    "sentence_step_mean": sum(s_steps) / len(s_steps),
                }
            except Exception as exc:
                logger.warning("sentence-level semantic detail skipped: %r", exc)
    return {
        "skipped": False,
        "warning": None,
        "steps": steps,
        "step_min": step_min_observed,
        "step_max": step_max,
        "step_mean": step_mean,
        "step_sd": step_sd,
        "dip_pair": dip_pair,
        "detail": detail,
        "failed": failed,
        "reason": reason,
        "flagged_blocks": flagged_blocks,
    }
