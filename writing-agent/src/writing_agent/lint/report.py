"""Lint report — aggregates all deterministic linters into one pass/fail gate."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..config import ThresholdSettings
from ..segment import is_code_block, is_heading_block
from .banned_tokens import scan_banned
from .burstiness import check_burstiness
from .deep_syntax import check_deep_syntax
from .redundancy import check_redundancy
from .transitions import check_transitions


class BlockFailure(BaseModel):
    block_index: int
    reasons: list[str]


class LintReport(BaseModel):
    passed: bool
    flagged_blocks: list[int]
    failures: list[BlockFailure]
    metrics: dict[str, Any] = Field(default_factory=dict)

    def scorecard(self) -> str:
        header = " blk     SD    var  banned  deep  note"
        lines = ["LINT SCORECARD", header, "-" * len(header)]
        m = self.metrics
        burst = {b["block_index"]: b for b in m.get("burstiness", [])}
        banned = {b["block_index"]: b for b in m.get("banned", [])}
        deep = {d["block_index"]: d for d in m.get("deep_syntax", [])}
        for idx in sorted(set(burst) | set(banned) | set(deep)):
            b = burst.get(idx, {})
            note = "; ".join(
                r for f in self.failures if f.block_index == idx for r in f.reasons
            )
            lines.append(
                f"{idx:>3}  {b.get('std_dev', 0):>5.1f}  {b.get('variance', 0):>6.1f}"
                f"  {banned.get(idx, {}).get('count', 0):>6}"
                f"  {deep.get(idx, {}).get('total', 0):>4}  {note or 'ok'}"
            )
        tr = m.get("transitions", {})
        lines.append("")
        lines.append(
            f"transition density {tr.get('doc_density', 0):.2f} "
            f"(max 0.15); em-dash/1k by block: {tr.get('em_dash_per_1k', {}) or {}}"
        )
        lines.append(f"flagged blocks: {self.flagged_blocks or []}")
        lines.append(f"RESULT: {'PASS' if self.passed else 'FAIL'}")
        return "\n".join(lines)


def run_all_linters(blocks: list[str], t: ThresholdSettings) -> LintReport:
    prose = [
        i
        for i, b in enumerate(blocks)
        if not is_code_block(b) and not is_heading_block(b)
    ]
    banned = scan_banned(blocks)
    burst = check_burstiness(
        blocks, sd_min=t.sentence_sd_min, variance_min=t.sentence_variance_min
    )
    trans = check_transitions(
        blocks,
        density_max=t.transition_density_max,
        em_dash_per_1k_max=t.em_dash_per_1k_max,
    )
    deep = check_deep_syntax(blocks, min_per_block=t.deep_syntax_min_per_block)
    redundancy = check_redundancy(blocks, jaccard_max=t.redundancy_jaccard_max)

    by_block: dict[int, list[str]] = {}
    for hit in banned:
        if hit.block_index in prose:
            by_block.setdefault(hit.block_index, []).append(
                f"banned n-grams ({hit.count}): {hit.pattern}"
            )
    for b in burst:
        if b.failed and b.block_index in prose:
            by_block.setdefault(b.block_index, []).append(b.reason or "burstiness")
    for idx in trans.transition_block_indices:
        if idx in prose:
            by_block.setdefault(idx, []).append(
                "paragraph opens with a transition — start with a concrete noun or action"
            )
    for d in deep:
        if d.failed:
            by_block.setdefault(d.block_index, []).append(d.reason or "deep syntax")
    for pair in redundancy.pairs:
        # Flag the LATER block: it is the one re-explaining.
        by_block.setdefault(pair.block_b, []).append(
            f"restates block {pair.block_a} "
            f"(4-gram overlap {pair.jaccard:.0%}"
            + (f", e.g. “{pair.shared[0]}”" if pair.shared else "")
            + ") — advance the point instead of repeating it"
        )

    flagged = sorted(idx for idx in by_block if idx in prose)
    return LintReport(
        passed=not flagged and not trans.failed,
        flagged_blocks=flagged,
        failures=[
            BlockFailure(block_index=idx, reasons=reasons)
            for idx, reasons in sorted(by_block.items())
            if idx in prose
        ],
        metrics={
            "banned": [h._asdict() for h in banned],
            "burstiness": [
                {
                    "block_index": b.block_index,
                    "mean": b.mean,
                    "std_dev": b.std_dev,
                    "variance": b.variance,
                }
                for b in burst
            ],
            "transitions": {
                "doc_density": trans.doc_density,
                "transition_block_indices": trans.transition_block_indices,
                "em_dash_per_1k": {str(k): v for k, v in trans.em_dash_per_1k.items()},
                "reason": trans.reason,
            },
            "deep_syntax": [
                {"block_index": d.block_index, "counts": d.counts, "total": d.total}
                for d in deep
            ],
            "redundancy": [
                {
                    "block_a": p.block_a,
                    "block_b": p.block_b,
                    "jaccard": p.jaccard,
                    "shared": p.shared,
                }
                for p in redundancy.pairs
            ],
        },
    )
