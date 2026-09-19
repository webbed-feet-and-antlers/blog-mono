"""Continuation-predictability scorer — the information-theoretic slop gate.

Method (2026-09 redesign): the legacy teacher-forced path (completions
echo+logprobs) no longer works on OpenRouter — every model returns
logprobs=null and logprobs>=1 requests are mistranslated. The modern
equivalent: give a scoring model the document-so-far ONLY, ask it to
continue, and measure how confidently it reproduces the author's actual
next sentences:

  - overlap: token overlap between the blind continuation and the real text
  - mean_bits: mean surprisal (-logprob/ln 2) of the generated tokens, when
    the provider returns logprobs (optional)

Aligned/machine prose is continued near-verbatim with high confidence —
that IS statistical normalization, measured directly. Human prose makes a
blind model diverge or hesitate. The scored text never appears in the
prompt, so there is no answer-in-prompt leak.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from ..llm import chat_score
from ..segment import is_code_block, is_heading_block, split_blocks

logger = logging.getLogger(__name__)

PEAK_BITS = 4.0
_PREFIX_CHARS = 2500


def stats_from_logprobs(logprobs: list[float]) -> dict[str, Any]:
    surprisal = [-lp / math.log(2) for lp in logprobs]
    n = len(surprisal)
    mean = sum(surprisal) / n if n else 0.0
    variance = sum((s - mean) ** 2 for s in surprisal) / n if n else 0.0
    return {
        "n_tokens": n,
        "mean_bits": mean,
        "variance_bits": variance,
        "peak_count": sum(1 for s in surprisal if s > PEAK_BITS),
    }


def _halves(block: str) -> tuple[str, str] | None:
    """Split a raw prose block into (first_half, second_half) on sentence
    boundaries. None when the block is too short to split meaningfully."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", block.strip()) if s.strip()]
    if len(sentences) < 3:
        return None
    k = max(1, len(sentences) // 2)
    first = " ".join(sentences[:k])
    second = " ".join(sentences[k:])
    if len(second.split()) < 5:
        return None
    return first, second


def token_overlap(generated: str, target: str) -> float:
    """Fraction of target tokens the blind continuation reproduced.

    The generation is truncated to ~1.3x the target length first — a model
    that rambles past the target would otherwise accumulate overlap by
    sheer volume.
    """
    tgt = re.findall(r"[a-z0-9']+", target.lower())
    if not tgt:
        return 0.0
    gen_tokens = re.findall(r"[a-z0-9']+", generated.lower())
    gen_tokens = gen_tokens[: int(len(tgt) * 1.3) + 1]
    common = Counter(gen_tokens) & Counter(tgt)
    return sum(common.values()) / len(tgt)


async def score_blocks(
    blocks: list[str],
    *,
    overlap_max: float = 0.55,
    bits_max: float = 1.2,
    llm=chat_score,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    flagged: list[int] = []
    try:
        for idx, block in enumerate(blocks):
            if is_code_block(block) or is_heading_block(block):
                continue
            halves = _halves(block)
            if halves is None:
                continue  # too short to judge predictability
            first, target = halves
            prefix = "\n\n".join(blocks[:idx] + [first])[-_PREFIX_CHARS:]
            logger.info(
                "surprisal: scoring block %d/%d (target %d words)",
                idx,
                len(blocks) - 1,
                len(target.split()),
            )
            text, lps = await llm(prefix)
            overlap = token_overlap(text, target)
            bits = stats_from_logprobs(lps)["mean_bits"] if lps else None
            # Primary signal: a blind model continues slop contexts
            # over-confidently (measured gap: ~0.6 bits on generic prose
            # vs ~1.1 on distinctive prose). Overlap is only the fallback
            # when the provider returns no logprobs.
            if bits is not None:
                failed = bits <= bits_max
            else:
                failed = overlap >= overlap_max
            reason = None
            if failed:
                reason = (
                    f"continuation predictability: blind scorer continued "
                    f"this context at {bits:.2f} bits mean surprisal "
                    f"(<= {bits_max}) — statistically normalized register"
                    if bits is not None
                    else f"blind scorer reproduced {overlap:.0%} of this "
                    f"block — statistically normalized register"
                )
                flagged.append(idx)
            results.append(
                {
                    "block_index": idx,
                    "overlap": overlap,
                    "mean_bits": bits,
                    "n_gen_tokens": len(lps),
                    "failed": failed,
                    "reason": reason,
                }
            )
    except Exception as exc:  # scorer unavailable → skip the gate, warn
        logger.warning("surprisal gate skipped: %r", exc)
        return {
            "skipped": True,
            "warning": f"surprisal scoring skipped: {exc}",
            "blocks": [],
            "flagged_blocks": [],
        }
    return {
        "skipped": False,
        "warning": None,
        "blocks": results,
        "flagged_blocks": flagged,
    }


async def calibrate(files: list[Path], *, llm=chat_score) -> dict[str, Any]:
    """Score human reference posts; suggest an overlap gate above them.

    Human references define 'normal' predictability for your topics — the
    default thresholds are starting points, not laws.
    """
    overlaps: list[float] = []
    bits: list[float] = []
    for f in files:
        blocks = split_blocks(f.read_text())
        for idx, block in enumerate(blocks):
            if is_code_block(block) or is_heading_block(block):
                continue
            halves = _halves(block)
            if halves is None:
                continue
            first, target = halves
            prefix = "\n\n".join(blocks[:idx] + [first])[-_PREFIX_CHARS:]
            text, lps = await llm(prefix)
            overlaps.append(token_overlap(text, target))
            if lps:
                bits.append(stats_from_logprobs(lps)["mean_bits"])

    def pct(xs: list[float], p: float) -> float | None:
        if not xs:
            return None
        xs = sorted(xs)
        k = max(0, min(len(xs) - 1, int(p * (len(xs) - 1))))
        return xs[k]

    suggested = pct(overlaps, 0.90)
    return {
        "block_overlaps": overlaps,
        "block_mean_bits": bits,
        "human_overlap_p90": suggested,
        "suggested_overlap_max": (
            round(min(0.85, suggested + 0.05), 2) if suggested is not None else None
        ),
    }
