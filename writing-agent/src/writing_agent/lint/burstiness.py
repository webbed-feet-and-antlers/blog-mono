"""Sentence burstiness — the rhythmic fingerprint of human writing.

Aligned prose converges on a narrow sentence-length band (flattened
uncertainty); human prose mixes 4-word punches with 35-word compound
thoughts. Gates: per-block SD and variance of sentence word counts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..segment import split_sentences, word_count


@dataclass
class BurstinessResult:
    block_index: int
    mean: float
    std_dev: float
    variance: float
    sentence_lengths: list[int] = field(repr=False)
    failed: bool
    reason: str | None = None


def check_burstiness(
    blocks: list[str],
    *,
    sd_min: float = 8.0,
    variance_min: float = 15.0,
) -> list[BurstinessResult]:
    results = []
    for idx, block in enumerate(blocks):
        lengths = [word_count(s) for s in split_sentences(block)]
        # <3 sentences can't demonstrate rhythm (the stylist's outros are
        # deliberately 2-4 sentences) — judging them is an unwinnable gate.
        if len(lengths) < 3:
            results.append(BurstinessResult(idx, 0.0, 0.0, 0.0, lengths, False))
            continue
        mean = sum(lengths) / len(lengths)
        variance = sum((x - mean) ** 2 for x in lengths) / len(lengths)
        std_dev = variance**0.5
        failed = std_dev < sd_min or variance < variance_min
        reason = None
        if failed:
            bits = []
            if std_dev < sd_min:
                bits.append(f"sentence-length SD {std_dev:.1f} < {sd_min}")
            if variance < variance_min:
                bits.append(f"variance {variance:.1f} < {variance_min}")
            reason = "rhythmic monotony: " + "; ".join(bits)
        results.append(
            BurstinessResult(idx, mean, std_dev, variance, lengths, failed, reason)
        )
    return results
