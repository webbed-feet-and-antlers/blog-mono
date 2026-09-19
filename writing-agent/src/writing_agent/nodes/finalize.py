"""Finalize node — assemble the post and scorecard (pure, no IO)."""

from __future__ import annotations

from typing import Any

from ..state import WritingState


def finalize_node(state: WritingState) -> dict[str, Any]:
    post = "\n\n".join(state["draft_blocks"])
    lint = state.get("lint_report", {})
    surprisal = state.get("surprisal_report", {})
    lines = ["FINAL SCORECARD", f"blocks: {len(state['draft_blocks'])}"]
    if lint:
        lines.append(f"lint passed: {lint.get('passed')}")
        lines.append(f"flagged: {lint.get('flagged_blocks')}")
    if surprisal.get("skipped"):
        lines.append(f"surprisal: SKIPPED ({surprisal.get('warning')})")
    elif surprisal.get("blocks"):
        parts = []
        for b in surprisal["blocks"]:
            s = f"#{b['block_index']} ov={b['overlap']:.2f}"
            if b.get("mean_bits") is not None:
                s += f" bits={b['mean_bits']:.2f}"
            parts.append(s)
        lines.append(f"continuation predictability by block: {', '.join(parts)}")
    lines.append(f"revisions: {state.get('revision_count', 1)}")
    disc = state.get("discourse_report")
    if disc and disc.get("metrics"):
        dm = disc["metrics"]
        lines.append(
            f"shape: temporal+retro/1k "
            f"{dm['temporal_jumps_per_1k'] + dm['retro_explanations_per_1k']:.1f}, "
            f"unresolved {dm['unresolved_markers']}, "
            f"summary markers {len(dm['summary_hit_blocks'])}, "
            f"top-5 share {dm['top5_content_share']:.0%}"
        )
        if disc.get("judge"):
            flagged_axes = [
                k for k, v in disc["judge"].items()
                if k != "notes" and v >= 0.7
            ]
            lines.append(f"shape judge: {'clean' if not flagged_axes else 'FLAGGED ' + ', '.join(flagged_axes)}")
    return {"final_post": post, "scorecard": "\n".join(lines)}
