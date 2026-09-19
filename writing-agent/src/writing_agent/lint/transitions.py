"""Transition-density + em-dash linter — SDH surface-marker checks.

Two failure modes from the research: (1) AI drafts open paragraph after
paragraph with conjunctive adverbs instead of concrete nouns/actions;
(2) aligned prose over-uses em-dashes — a surface marker that amplifies
while deep syntax collapses (Structural Depth Hypothesis).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..segment import is_code_block, is_heading_block

_TRANSITIONS = {
    "furthermore", "additionally", "moreover", "however",
    "consequently", "therefore", "nevertheless", "nonetheless", "thus",
    "firstly", "secondly", "finally", "overall",
}
# "in" is only banned as the start of specific phrases, not as a word.
_IN_PHRASES = ("in addition", "in conclusion", "in summary", "in short")


@dataclass
class TransitionResult:
    doc_density: float
    transition_block_indices: list[int] = field(default_factory=list)
    em_dash_per_1k: dict[int, float] = field(default_factory=dict)
    failed: bool = False
    reason: str | None = None


def _opens_with_transition(paragraph: str) -> bool:
    lowered = paragraph.lower()
    if lowered.startswith(_IN_PHRASES):
        return True
    first = re.match(r"[a-z]+", lowered)
    return bool(first and first.group(0) in _TRANSITIONS)


def check_transitions(
    blocks: list[str],
    *,
    density_max: float = 0.15,
    em_dash_per_1k_max: float = 6.0,
) -> TransitionResult:
    paragraphs = 0
    openings = 0
    transition_blocks: list[int] = []
    em_dash: dict[int, float] = {}
    reasons: list[str] = []

    for idx, block in enumerate(blocks):
        if is_code_block(block) or is_heading_block(block):
            continue
        for para in (p.strip() for p in block.split("\n\n") if p.strip()):
            paragraphs += 1
            if _opens_with_transition(para):
                openings += 1
                if idx not in transition_blocks:
                    transition_blocks.append(idx)
        words = len(block.split())
        if words:
            density = block.count("—") / words * 1000
            em_dash[idx] = density
            if density > em_dash_per_1k_max:
                reasons.append(
                    f"em-dash density {density:.1f}/1k in block {idx} "
                    f"(max {em_dash_per_1k_max})"
                )

    doc_density = openings / paragraphs if paragraphs else 0.0
    if doc_density >= density_max:
        reasons.insert(
            0,
            f"{openings}/{paragraphs} paragraphs open with a transition "
            f"(density {doc_density:.2f} >= {density_max})",
        )
    return TransitionResult(
        doc_density=doc_density,
        transition_block_indices=transition_blocks,
        em_dash_per_1k=em_dash,
        failed=bool(reasons),
        reason="; ".join(reasons) if reasons else None,
    )
