"""Audit node — deterministic linters + surprisal gate in one checkpoint."""

from __future__ import annotations

import logging
from typing import Any

from ..config import get_settings
from ..lint.report import BlockFailure, LintReport, run_all_linters
from ..scoring.discourse import analyze_discourse
from ..scoring.semantic import semantic_report
from ..scoring.surprisal import score_blocks
from ..segment import is_code_block, is_heading_block
from ..state import WritingState

logger = logging.getLogger(__name__)


def _merge_failures(lint: LintReport, failures: list[dict[str, Any]]) -> None:
    """Fold discourse failures (dicts with block_index/reason) into the lint
    report so the editor sees one unified defect list."""
    by_index: dict[int, list[str]] = {}
    for f in failures:
        idx = f.get("block_index")
        if idx is not None:
            by_index.setdefault(idx, []).append(f["reason"])
    for idx, reasons in by_index.items():
        existing = next((f for f in lint.failures if f.block_index == idx), None)
        if existing is not None:
            existing.reasons.extend(reasons)
        else:
            lint.failures.append(BlockFailure(block_index=idx, reasons=reasons))


async def audit_node(
    state: WritingState,
    scorer=score_blocks,
    discourse=analyze_discourse,
    semantic=semantic_report,
) -> dict[str, Any]:
    blocks = state["draft_blocks"]
    settings = get_settings()
    lint = run_all_linters(blocks, settings.thresholds)
    surprisal = await scorer(
        blocks,
        overlap_max=settings.thresholds.continuation_overlap_max,
        bits_max=settings.thresholds.continuation_bits_max,
        convergence_max=settings.thresholds.continuation_convergence_max,
        observer_model=settings.models.observer,
        corroboration_min=settings.thresholds.observer_corroboration_min,
    )
    semantic_out = await semantic(
        blocks, step_min=settings.thresholds.semantic_step_min
    )
    if semantic_out.get("failed") and semantic_out.get("flagged_blocks"):
        _merge_failures(
            lint,
            [
                {
                    "block_index": semantic_out["flagged_blocks"][0],
                    "reason": semantic_out["reason"],
                }
            ],
        )

    flagged = sorted(set(lint.flagged_blocks) | set(surprisal["flagged_blocks"]))
    discourse_report = await discourse(blocks, settings.thresholds)
    _merge_failures(lint, discourse_report["failures"])
    flagged = sorted(set(flagged) | set(discourse_report["flagged_blocks"]))
    logger.info(
        "audit: lint_flagged=%s surprisal_flagged=%s skipped=%s "
        "discourse_flagged=%s → merged=%s",
        lint.flagged_blocks,
        surprisal.get("flagged_blocks"),
        surprisal.get("skipped"),
        discourse_report["flagged_blocks"],
        flagged,
    )
    if (not lint.passed or not discourse_report["passed"]) and not flagged:
        # Doc-level failure with no prose block flagged — give the editor
        # the first prose block as the target.
        prose = [
            i
            for i, b in enumerate(blocks)
            if not is_code_block(b) and not is_heading_block(b)
        ]
        if prose:
            flagged = [prose[0]]
            lint.failures.append(
                BlockFailure(
                    block_index=prose[0],
                    reasons=[
                        "doc-level style failure — see metrics in the scorecard"
                    ],
                )
            )

    return {
        "lint_report": lint.model_dump(),
        "surprisal_report": surprisal,
        "discourse_report": discourse_report,
        "semantic_report": semantic_out,
        "flagged_blocks": flagged,
    }
