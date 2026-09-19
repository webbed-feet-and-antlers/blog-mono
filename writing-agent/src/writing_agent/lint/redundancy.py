"""Cross-section redundancy — the self-BLEU check.

AI text re-explains the same idea across sections (StoryScope's
over-explanation finding; the asymmetric-agency paper's "models elaborate
instead of introduce"). We measure 4-gram Jaccard overlap between every
pair of substantial prose sections and flag pairs that share too much
wording — the later block should advance the point, not restate it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations

from ..segment import is_code_block, is_heading_block, word_count


@dataclass
class RedundantPair:
    block_a: int
    block_b: int
    jaccard: float
    shared: list[str] = field(default_factory=list)


@dataclass
class RedundancyResult:
    pairs: list[RedundantPair] = field(default_factory=list)
    failed: bool = False


def _ngrams(text: str, n: int = 4) -> set[str]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def check_redundancy(
    blocks: list[str], *, jaccard_max: float = 0.25, min_words: int = 30
) -> RedundancyResult:
    substantial = [
        (i, b)
        for i, b in enumerate(blocks)
        if not is_code_block(b) and not is_heading_block(b) and word_count(b) >= min_words
    ]
    pairs: list[RedundantPair] = []
    for (ia, ba), (ib, bb) in combinations(substantial, 2):
        grams_a, grams_b = _ngrams(ba), _ngrams(bb)
        union = grams_a | grams_b
        if not union:
            continue
        jaccard = len(grams_a & grams_b) / len(union)
        if jaccard >= jaccard_max:
            pairs.append(
                RedundantPair(
                    block_a=ia,
                    block_b=ib,
                    jaccard=jaccard,
                    shared=sorted(grams_a & grams_b)[:2],
                )
            )
    return RedundancyResult(pairs=pairs, failed=bool(pairs))
