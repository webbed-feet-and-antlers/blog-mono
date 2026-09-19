"""Text segmentation — markdown → blocks → sentences.

Blocks are the unit of drafting, linting, and targeted editing. A block is a
heading, a paragraph, or a fenced code block (fences stay intact — code is
never sentence-split or linted as prose).
"""

from __future__ import annotations

import re


def split_blocks(markdown: str) -> list[str]:
    """Split markdown into top-level blocks.

    Separators: blank lines and heading starts. Fenced code blocks are kept
    intact, including any blank lines inside them.
    """
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False

    def flush() -> None:
        if current:
            blocks.append("\n".join(current).strip())
            current.clear()

    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            current.append(line)
            continue
        if in_fence:
            current.append(line)
            continue
        if not stripped:
            flush()
            continue
        if stripped.startswith("#") and current:
            flush()
        current.append(line)
    flush()
    return [b for b in blocks if b]


def split_sentences(block: str) -> list[str]:
    """Split a prose block into sentences.

    Fenced code lines are skipped entirely; inline code spans are replaced
    with CODE so `foo.bar` doesn't produce fake sentence boundaries.
    """
    lines: list[str] = []
    in_fence = False
    for line in block.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    text = " ".join(ln.strip() for ln in lines if ln.strip())
    text = re.sub(r"`[^`]*`", "CODE", text)
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]
    # Known v1 limitation: abbreviations (e.g., "e.g.") can split early.
    # Acceptable — thresholds are statistical, not per-sentence.


def word_count(text: str) -> int:
    return len([w for w in text.split() if w.strip()])


def is_code_block(block: str) -> bool:
    return block.lstrip().startswith("```")


def is_heading_block(block: str) -> bool:
    return block.lstrip().startswith("#")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "draft"
