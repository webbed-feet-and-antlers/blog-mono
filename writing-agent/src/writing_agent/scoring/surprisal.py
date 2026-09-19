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

import asyncio
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
SAMPLE_TEMPERATURE = 0.8


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
    sd_min: float = 0.15,
    convergence_max: float = 0.5,
    samples: int = 3,
    observer_model: str = "",
    corroboration_min: float = 0.5,
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

            # Fast-DetectGPT-style curvature, adapted: sample several
            # continuations at temperature and measure how much they agree
            # with EACH OTHER. Machine-shaped contexts make the model
            # converge on near-identical continuations; distinctive
            # contexts produce genuinely different guesses.
            agreement: float | None = None
            if samples >= 2:
                sampled = await asyncio.gather(
                    *[
                        llm(prefix, temperature=SAMPLE_TEMPERATURE)
                        for _ in range(samples)
                    ],
                    return_exceptions=True,
                )
                sample_texts = [
                    t
                    for r in sampled
                    if not isinstance(r, BaseException)
                    for t, _ in [r]
                ]
                if len(sample_texts) >= 2:
                    pairs = [
                        (a, b)
                        for i, a in enumerate(sample_texts)
                        for b in sample_texts[i + 1 :]
                    ]
                    agreement = sum(
                        (token_overlap(a, b) + token_overlap(b, a)) / 2
                        for a, b in pairs
                    ) / len(pairs)

            if bits is not None:
                failed = bits <= bits_max
            else:
                failed = overlap >= overlap_max

            # Optional Binoculars-style second observer (different model
            # family, no logprobs needed): textual overlap between its
            # continuation and the primary's. Corroborated predictability
            # is stronger evidence than single-model predictability.
            corroboration: float | None = None
            if observer_model:
                try:
                    obs_text, _ = await llm(
                        prefix, model=observer_model, temperature=0.0, logprobs=False
                    )
                    corroboration = token_overlap(obs_text, text)
                except Exception as exc:
                    logger.warning("observer call failed: %r", exc)

            if agreement is not None and agreement >= convergence_max:
                failed = True
            if (
                corroboration is not None
                and corroboration >= corroboration_min
                and agreement is not None
                and agreement >= convergence_max
            ):
                failed = True
            reason = None
            if failed:
                if (
                    corroboration is not None
                    and corroboration >= corroboration_min
                ):
                    reason = (
                        f"cross-model convergence: two model families agree "
                        f"on where this text goes (observer overlap "
                        f"{corroboration:.0%}"
                        + (
                            f", samples agree on {agreement:.0%}"
                            if agreement is not None
                            else ""
                        )
                        + ")"
                    )
                elif agreement is not None and agreement >= convergence_max:
                    reason = (
                        f"convergent continuations: {len(sample_texts)} blind "
                        f"samples agree on {agreement:.0%} of tokens — the "
                        f"model already knows where this text goes"
                    )
                elif bits is not None:
                    reason = (
                        f"continuation predictability: blind scorer continued "
                        f"this context at {bits:.2f} bits mean surprisal "
                        f"(<= {bits_max}) — statistically normalized register"
                    )
                else:
                    reason = (
                        f"blind scorer reproduced {overlap:.0%} of this "
                        f"block — statistically normalized register"
                    )
                flagged.append(idx)
            results.append(
                {
                    "block_index": idx,
                    "overlap": overlap,
                    "mean_bits": bits,
                    "agreement": agreement,
                    "corroboration": corroboration,
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
    # Doc-level predictability burstiness (GPTZero-style): human writing's
    # per-block surprisal VARIES; uniformly predictable documents read
    # machine even when each block individually clears the bits gate.
    burstiness: dict[str, Any] | None = None
    bits_values = [r["mean_bits"] for r in results if r["mean_bits"] is not None]
    if len(bits_values) >= 4:
        mean_bits_doc = sum(bits_values) / len(bits_values)
        bits_sd = (sum((x - mean_bits_doc) ** 2 for x in bits_values) / len(bits_values)) ** 0.5
        burstiness = {"bits_sd": bits_sd, "sd_min": sd_min, "failed": bits_sd < sd_min}
        if burstiness["failed"]:
            flattest = min(
                (r for r in results if r["mean_bits"] is not None),
                key=lambda r: r["mean_bits"],
            )
            flattest["failed"] = True
            flattest["reason"] = (
                f"uniformly predictable document: per-block surprisal SD "
                f"{bits_sd:.2f} < {sd_min}; this is the flattest block "
                f"({flattest['mean_bits']:.2f} bits) — make something here "
                f"surprise the reader"
            )
            if flattest["block_index"] not in flagged:
                flagged.append(flattest["block_index"])
    return {
        "skipped": False,
        "warning": None,
        "blocks": results,
        "flagged_blocks": sorted(flagged),
        "bits_burstiness": burstiness,
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
