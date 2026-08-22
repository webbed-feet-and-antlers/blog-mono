"""Eval-harness configuration: paths + case-count knobs.

The judge model and default case count live in app.config.settings
(EVALS_JUDGE_MODEL / EVALS_N) so they share the app's .env loading. This
module only adds filesystem layout for datasets and reports.
"""

from __future__ import annotations

import os
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

# How many generation chains / judge calls may be in flight at once. The
# generation tools are pure async functions (no DB) and the OpenAI client
# pools connections, so the per-case work parallelizes safely; 4 keeps us
# under OpenRouter rate limits (the empty-response retry absorbs bursts).
EVALS_CONCURRENCY = max(1, int(os.environ.get("EVALS_CONCURRENCY", "4")))

# Which prepared dataset split the suites draw from (see evals.data):
#   train — scratch pool for exploratory runs and any future fitting
#   val   — the everyday pool: gate/full runs + committed baselines
#   test  — held-out overfitting check; run rarely, never tune against it
# Nothing in the harness fits parameters, so "overfitting" here means tuning
# prompts/gates against a fixed sample — the val-vs-test gap is the signal.
EVALS_SPLIT = os.environ.get("EVALS_SPLIT", "val")
if EVALS_SPLIT not in ("train", "val", "test"):
    raise SystemExit(
        f"EVALS_SPLIT must be train, val, or test — got {EVALS_SPLIT!r}"
    )


def case_limit(default: int = 25) -> int:
    """Cases per suite, from EVALS_N (via settings.evals_n)."""
    return max(1, settings.evals_n)


def sample_cases(cases: list, n: int | None = None) -> list:
    """Deterministic fixed-seed sample of at most `n` cases.

    One seeded draw of _MAX_SAMPLE cases, sliced to n — so a small-N run
    (the gate tier, EVALS_N=3) works over a strict subset of the full
    N=10 set and gate numbers roll up coherently into full-run trends."""
    import random

    n = case_limit() if n is None else n
    if len(cases) <= n:
        return cases
    rng = random.Random(SAMPLE_SEED)
    ordered = rng.sample(cases, min(len(cases), _MAX_SAMPLE))
    return ordered[: min(n, len(ordered))]


_MAX_SAMPLE = 25  # cap on the seeded draw; full-run EVALS_N must stay ≤ this
