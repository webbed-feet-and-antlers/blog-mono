from writing_agent.config import ThresholdSettings
from writing_agent.lint.report import run_all_linters
from writing_agent.segment import split_blocks

SLOP = """## Overview

Furthermore, it is important to note that this approach is robust and it
delivers seamless integration for every user. Additionally, the framework
is a testament to good design across the board. Moreover, teams must
consider the implications carefully before adopting anything new here.
"""

HUMAN = """## Setup

When the indexer collapsed, everything stopped. We had no runbook (nobody
expected the primary to fail during a rolling upgrade, which in hindsight
was optimistic). Had we chosen Kafka two years earlier the failure mode
would have been different, though maybe not better: the queue would have
absorbed the burst, the consumers would have lagged instead of crashing,
and honestly I still do not know which failure I prefer. We wrote the
postmortem that night.

```python

x = 1
```
"""


def test_slop_doc_fails_and_flags_prose_block():
    report = run_all_linters(split_blocks(SLOP), ThresholdSettings())
    assert not report.passed
    assert 1 in report.flagged_blocks  # the prose block, not the heading
    reasons = next(f.reasons for f in report.failures if f.block_index == 1)
    assert any("banned" in r.lower() for r in reasons)
    assert any("monotony" in r for r in reasons)


def test_human_doc_passes_with_code_exempt():
    report = run_all_linters(split_blocks(HUMAN), ThresholdSettings())
    assert report.passed, report.failures
    assert report.flagged_blocks == []


def test_scorecard_renders_text_table():
    report = run_all_linters(split_blocks(SLOP), ThresholdSettings())
    card = report.scorecard()
    assert "block" in card.lower()
    assert "FAIL" in card
    assert "\x1b" not in card  # no ANSI codes


REDUNDANT_DOC = """## Incident

When the indexer collapsed, everything stopped. We had no runbook (nobody
expected the primary to fail during a rolling upgrade, which in hindsight
was optimistic). Had we chosen Kafka two years earlier the failure mode
would have been different, though maybe not better, and I still do not
know which failure I prefer. The postmortem ran long.

When the indexer collapsed, everything stopped. We had no runbook (nobody
expected the primary to fail during a rolling upgrade, which in hindsight
was optimistic). Had we chosen Kafka two years earlier the failure mode
would have been different, though maybe not better, and I still do not
know which failure I prefer. The postmortem ran long. The queue absorbed
nothing that night at all.
"""


def test_redundant_doc_flags_the_re_explaining_block():
    report = run_all_linters(split_blocks(REDUNDANT_DOC), ThresholdSettings())
    assert not report.passed
    assert report.flagged_blocks == [2]  # the later, repeating block
    reasons = next(f.reasons for f in report.failures if f.block_index == 2)
    assert any("restates block 1" in r for r in reasons)
    assert report.metrics["redundancy"][0]["jaccard"] > 0.5
