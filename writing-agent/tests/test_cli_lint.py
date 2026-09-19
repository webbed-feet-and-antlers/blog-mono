from pathlib import Path

from typer.testing import CliRunner

from writing_agent.cli import app

runner = CliRunner()

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
"""


def test_lint_slop_exits_nonzero(tmp_path: Path):
    f = tmp_path / "slop.md"
    f.write_text(SLOP)
    result = runner.invoke(app, ["lint", str(f)])
    assert "FAIL" in result.output
    assert result.exit_code == 1


def test_lint_human_passes(tmp_path: Path):
    f = tmp_path / "human.md"
    f.write_text(HUMAN)
    result = runner.invoke(app, ["lint", str(f)])
    assert "PASS" in result.output
    assert result.exit_code == 0
