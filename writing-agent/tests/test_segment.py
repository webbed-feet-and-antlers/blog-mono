from writing_agent.segment import (
    is_code_block,
    is_heading_block,
    slugify,
    split_blocks,
    split_sentences,
    word_count,
)

DOC = """## Setup

Install the thing:

```python
import re

def f():
    return 1
```

Then run it. It works.

Mostly.
"""


def test_split_blocks_keeps_headings_paragraphs_and_fences_intact():
    blocks = split_blocks(DOC)
    assert blocks[0] == "## Setup"
    assert blocks[1] == "Install the thing:"
    assert is_code_block(blocks[2])
    assert "import re" in blocks[2]
    assert blocks[3] == "Then run it. It works."
    assert blocks[4] == "Mostly."


def test_split_blocks_blank_lines_inside_fence_do_not_split():
    assert len([b for b in split_blocks(DOC) if is_code_block(b)]) == 1


def test_split_sentences_basic_and_inline_code():
    sents = split_sentences("Run `make build.py` first. Then deploy. Done?")
    assert sents == ["Run CODE first.", "Then deploy.", "Done?"]


def test_split_sentences_skips_code_fences():
    block = "Intro sentence.\n```python\nx = 1. Not prose.\n```\nOutro sentence."
    sents = split_sentences(block)
    assert sents == ["Intro sentence.", "Outro sentence."]


def test_predicates_word_count_and_slugify():
    assert is_heading_block("## Setup")
    assert not is_heading_block("Setup")
    assert word_count("one two three four") == 4
    assert slugify("Why Our Indexer Collapsed!") == "why-our-indexer-collapsed"
    assert slugify("???") == "draft"
