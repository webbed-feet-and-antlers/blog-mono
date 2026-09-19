"""Editor node — surgical rewrites of flagged blocks only.

Margin-aware in the Writing-RL sense: compute goes exclusively to the
blocks failing gates. Global re-generation is forbidden — it rolls new
stylistic dice on text that already passed.

With pairwise selection (Writing-RL's pairwise comparison reward): each
flagged block gets TWO candidate rewrites — a cold surgical pass at
editor_temp and a warmer variance pass at candidate_b_temp — and a
pairwise judge picks whichever clears the defects AND reads more human.
The loser is discarded before the audit ever sees it. Presentation order
swaps per block as a position-bias guard; any judge failure falls back to
the cold candidate, which is exactly the pre-pairwise behavior.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import get_settings
from ..llm import chat, chat_json
from ..state import WritingState

logger = logging.getLogger(__name__)

SYSTEM = """You are a surgical prose editor. Fix ONLY the listed defects.
Preserve every technical claim, number, command, code block, and markdown
heading. Do not restyle passages that are not named in the defects. Keep
the author's voice; make the minimum edits that clear the defects."""

VARIANCE_SYSTEM = """You are a surgical prose editor fixing listed defects.
Preserve every technical claim, number, command, code block, and markdown
heading. Within those constraints you may re-cut sentences for rhythm:
vary lengths hard, ground claims in concrete incidents, prefer
parenthetical asides over polish. No summary endings."""

_JUDGE_SYSTEM = """You are a pairwise revision judge for a technical blog.
Two candidates rewrote one flagged block. Pick the candidate that BOTH
clears the listed defects AND reads more human: varied sentence lengths,
concrete incidents and numbers, parenthetical asides over polish, no
summary/lesson endings, and every technical claim, code block, and
markdown heading preserved exactly. Ties go to the smaller diff.
Respond ONLY with JSON: {"choice": 1, "reason": "one sentence"}"""


def reasons_by_block(state: WritingState) -> dict[int, str]:
    merged: dict[int, list[str]] = {}
    for failure in state.get("lint_report", {}).get("failures", []):
        merged.setdefault(failure["block_index"], []).extend(failure["reasons"])
    for block in state.get("surprisal_report", {}).get("blocks", []):
        if block.get("failed") and block.get("reason"):
            merged.setdefault(block["block_index"], []).append(block["reason"])
    doc_reason = (
        state.get("lint_report", {}).get("metrics", {}).get("transitions", {}).get("reason")
    )
    if doc_reason:
        for idx in state.get("flagged_blocks", []):
            merged.setdefault(idx, []).append(doc_reason)
    return {idx: "; ".join(rs) for idx, rs in merged.items()}


async def _revise(llm, block: str, defect_text: str, temperature: float, system: str) -> str:
    prompt = (
        f"DEFECTS:\n{defect_text}\n\nBLOCK:\n{block}\n\n"
        "Rewrite the block clearing the defects. Keep markdown structure "
        "identical. Output ONLY the rewritten block."
    )
    return await llm(
        [{"role": "system", "content": system},
         {"role": "user", "content": prompt}],
        temperature=temperature,
    )


async def _judge_pick(
    judge, defect_text: str, original: str, c1: str, c2: str, cold_is_two: bool
) -> tuple[str, str]:
    """Returns (winner_text, note). Falls back to the cold candidate."""
    cold = c2 if cold_is_two else c1
    prompt = (
        f"DEFECTS:\n{defect_text}\n\nORIGINAL BLOCK:\n{original}\n\n"
        f"CANDIDATE 1:\n{c1}\n\nCANDIDATE 2:\n{c2}\n\n"
        "Which candidate clears the defects and reads more human? JSON only."
    )
    try:
        result = await judge(
            [{"role": "system", "content": _JUDGE_SYSTEM},
             {"role": "user", "content": prompt}],
            temperature=0.1,
        )
        choice = result.get("choice")
        if choice not in (1, 2):
            raise ValueError(f"invalid choice {choice!r}")
    except Exception as exc:
        logger.warning("pairwise judge failed (%r) — keeping cold candidate", exc)
        return cold, f"judge failed ({type(exc).__name__}) — cold candidate kept"
    winner = c1 if choice == 1 else c2
    note = f"candidate {choice}: {str(result.get('reason', ''))[:100]}"
    return winner, note


async def editor_node(
    state: WritingState, llm=chat, judge=chat_json, pairwise: bool | None = None
) -> dict[str, Any]:
    blocks = list(state["draft_blocks"])
    reasons = reasons_by_block(state)
    settings = get_settings()
    if pairwise is None:
        pairwise = settings.sampling.pairwise_selection
    temp = settings.sampling.editor_temp

    for idx in state.get("flagged_blocks", []):
        defect_text = reasons.get(
            idx, "failed one or more style gates; increase sentence-length variance"
        )
        if not pairwise:
            blocks[idx] = await _revise(llm, blocks[idx], defect_text, temp, SYSTEM)
            continue

        cold = await _revise(llm, blocks[idx], defect_text, temp, SYSTEM)
        warm = await _revise(
            llm,
            blocks[idx],
            defect_text,
            settings.sampling.candidate_b_temp,
            VARIANCE_SYSTEM,
        )
        # Position-bias guard: alternate which candidate is presented first.
        cold_is_two = idx % 2 == 1
        c1, c2 = (warm, cold) if cold_is_two else (cold, warm)
        winner, note = await _judge_pick(
            judge, defect_text, blocks[idx], c1, c2, cold_is_two
        )
        logger.info("editor: block %d pairwise pick — %s", idx, note)
        blocks[idx] = winner

    return {
        "draft_blocks": blocks,
        "revision_count": state.get("revision_count", 1) + 1,
    }
