"""Eval-harness configuration: paths + case-count knobs.

The judge model and default case count live in app.config.settings
(EVALS_JUDGE_MODEL / EVALS_N) so they share the app's .env loading. This
module only adds filesystem layout for datasets and reports.
"""

from __future__ import annotations

from pathlib import Path

from app.config import settings

# backend/evals/ — this package's directory.
EVALS_DIR = Path(__file__).resolve().parent

# Prepared datasets (gitignored). Populated by `python -m evals.data`.
DATA_DIR = EVALS_DIR / "data"

# Per-run JSON reports + committed baselines. EVALS.md is regenerated here.
REPORTS_DIR = EVALS_DIR / "reports"
BASELINES_DIR = REPORTS_DIR / "baselines"

# Fixed seed for sampling eval cases — makes runs comparable across time.
SAMPLE_SEED = 42


def case_limit(default: int = 25) -> int:
    """Cases per suite, from EVALS_N (via settings.evals_n)."""
    return max(1, settings.evals_n)


def sample_cases(cases: list, n: int | None = None) -> list:
    """Deterministic fixed-seed sample of at most `n` cases."""
    import random

    n = case_limit() if n is None else n
    if len(cases) <= n:
        return cases
    rng = random.Random(SAMPLE_SEED)
    return rng.sample(cases, n)
