"""Banned-token linter — regex scan against config/banned_ngrams.txt.

Hard-bans the high-frequency surface markers that RLHF amplifies (the
Structural Depth Hypothesis's "surface polish" failure mode).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from ..config import CONFIG_DIR


class BannedHit(NamedTuple):
    pattern: str  # comma-joined matched patterns
    block_index: int
    count: int


def load_patterns(path: Path | None = None) -> list[str]:
    path = path or CONFIG_DIR / "banned_ngrams.txt"
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def scan_banned(blocks: list[str], patterns: list[str] | None = None) -> list[BannedHit]:
    patterns = patterns if patterns is not None else load_patterns()
    hits: list[BannedHit] = []
    for idx, block in enumerate(blocks):
        matched = [pat for pat in patterns if re.search(pat, block, flags=re.IGNORECASE)]
        if matched:
            hits.append(BannedHit(", ".join(matched), idx, len(matched)))
    return hits
