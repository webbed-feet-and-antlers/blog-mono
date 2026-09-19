"""Deep-syntax linter — Structural Depth Hypothesis enforcer.

Aligned models prune mid/deep syntactic dependencies (parentheticals,
inversions, embedded clauses) while inflating surface connectives. This
linter counts the human markers and fails judged blocks that have none.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..segment import is_code_block, is_heading_block, split_sentences, word_count

_PARENTHETICAL = re.compile(r"\([^)]{15,}\)")
_EM_DASH_ASIDE = re.compile(r"—[^—\n]{15,}—")
_INVERSIONS = [
    re.compile(r"\b(?:Had|Were|Should) (?:we|I|they|you|it)\b"),
]


@dataclass
class DeepSyntaxResult:
    block_index: int
    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0
    failed: bool = False
    reason: str | None = None


def check_deep_syntax(
    blocks: list[str], *, min_per_block: int = 1
) -> list[DeepSyntaxResult]:
    results = []
    for idx, block in enumerate(blocks):
        if is_code_block(block) or is_heading_block(block):
            continue
        if len(split_sentences(block)) < 2 or word_count(block) < 40:
            continue  # too small to demand structural depth
        counts = {
            "parenthetical": len(_PARENTHETICAL.findall(block)),
            "em_dash_aside": len(_EM_DASH_ASIDE.findall(block)),
            "inversion": sum(rx.search(block) is not None for rx in _INVERSIONS),
        }
        total = sum(counts.values())
        failed = total < min_per_block
        results.append(
            DeepSyntaxResult(
                block_index=idx,
                counts=counts,
                total=total,
                failed=failed,
                reason=(
                    "no deep-syntax structures (parentheticals, inversions, "
                    f"asides) in a {word_count(block)}-word block"
                )
                if failed
                else None,
            )
        )
    return results
