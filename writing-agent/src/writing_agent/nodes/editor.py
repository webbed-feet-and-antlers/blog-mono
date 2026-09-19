"""Editor node — surgical rewrites of flagged blocks only.

Margin-aware in the Writing-RL sense: compute goes exclusively to the
blocks failing gates. Global re-generation is forbidden — it rolls new
stylistic dice on text that already passed.
"""

from __future__ import annotations

from typing import Any

from ..config import get_settings
from ..llm import chat
from ..state import WritingState

SYSTEM = """You are a surgical prose editor. Fix ONLY the listed defects.
Preserve every technical claim, number, command, code block, and markdown
heading. Do not restyle passages that are not named in the defects. Keep
the author's voice; make the minimum edits that clear the defects."""


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


async def editor_node(state: WritingState, llm=chat) -> dict[str, Any]:
    blocks = list(state["draft_blocks"])
    reasons = reasons_by_block(state)
    temp = get_settings().sampling.editor_temp
    for idx in state.get("flagged_blocks", []):
        defect_text = reasons.get(
            idx, "failed one or more style gates; increase sentence-length variance"
        )
        prompt = (
            f"DEFECTS:\n{defect_text}\n\nBLOCK:\n{blocks[idx]}\n\n"
            "Rewrite the block clearing the defects. Keep markdown structure "
            "identical. Output ONLY the rewritten block."
        )
        blocks[idx] = await llm(
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=temp,
        )
    return {
        "draft_blocks": blocks,
        "revision_count": state.get("revision_count", 1) + 1,
    }
