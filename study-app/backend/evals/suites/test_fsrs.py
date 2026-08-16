"""FSRS calibration suite — replay real forgetting-curve outcomes through
the production scheduler (Duolingo's public 13M-trace HLR dataset).

The question a scheduler exists to answer: *given everything observed so
far, what is the probability the learner recalls this item right now?*
For each (user, lexeme) trace we replay every prior practice session through
the app's real FSRS code path (binary rating mapping: correct → Good, wrong
→ Again, exactly what quiz/flashcard submissions do), then predict the
current session's recall two ways from the SAME scheduler state:

  - the fsrs library's native power-law forgetting curve, and
  - the app's `retrievability` (an exp(-t/S) approximation).

Observations are restricted to sessions at least MIN_DELTA_DAYS after the
previous one — the regime where a forgetting curve is the operative model
(59% of Duolingo sessions are same-day practice where R≈1 for every item
and ranking is pure noise).

What the first runs of this suite established (recorded as report-only
metrics so the numbers stay visible):

  1. The exp(-t/S) approximation is catastrophically miscalibrated
     (Brier 0.3-0.6 vs the power-law's ~0.08) — fine as a ranking key,
     wrong as a probability. Gated: the power law must dominate it.
  2. FSRS with default parameters beats chance and the last-outcome streak
     on ranking, but does NOT beat the running correct-rate baseline
     (item difficulty): on this material, what the learner got right
     before predicts the next session better than time-decay does. The
     fsrs parameters were fit on Anki data; Duolingo vocabulary is easier
     material with a ~93% base success rate. Not gated — a finding.
"""

from __future__ import annotations

import json as _json
from datetime import datetime, timezone

import pytest

from evals.config import DATA_DIR
from evals.metrics import auc, brier, log_loss, majority_accuracy
from evals.report import record

pytestmark = pytest.mark.evals

WARMUP_SEEN = 4  # skip the first encounters — let a history accumulate
MIN_DELTA_DAYS = 3  # forgetting-curve regime (not same-day practice)
MAX_OBSERVATIONS = 30_000

_REPLAY: dict | None = None


def _replay() -> dict:
    """One replay pass, shared by every test in this module."""
    global _REPLAY
    if _REPLAY is not None:
        return _REPLAY

    import pandas as pd
    from fsrs import Card, Rating

    from app.agent.fsrs_scheduler import (
        _scheduler,
        _state_to_card_json,
        retrievability,
    )

    path = DATA_DIR / "duolingo_sample.parquet"
    if not path.exists():
        pytest.fail(
            "duolingo_sample.parquet missing — run `uv run python -m evals.data duolingo`"
        )

    df = pd.read_parquet(
        path,
        columns=[
            "user_id", "lexeme_id", "timestamp", "delta", "p_recall",
            "history_seen", "history_correct",
        ],
    )
    traces: dict[tuple, list[tuple]] = {}
    for row in df.itertuples(index=False):
        traces.setdefault((row.user_id, row.lexeme_id), []).append(
            (
                int(row.timestamp),
                float(row.delta),
                1 if float(row.p_recall) >= 0.5 else 0,
                int(row.history_seen),
                int(row.history_correct),
            )
        )

    power_preds: list[float] = []
    exp_preds: list[float] = []
    outcomes: list[int] = []
    streak_preds: list[float] = []
    rate_preds: list[float] = []

    for (uid, lex), rows in sorted(traces.items()):
        if len(outcomes) >= MAX_OBSERVATIONS:
            break
        rows.sort(key=lambda r: r[0])
        fsrs_state = None
        last_outcome: int | None = None
        for ts, delta, outcome, hseen, hcorr in rows:
            now = datetime.fromtimestamp(ts, tz=timezone.utc)

            # Predict BEFORE folding in this session's outcome.
            if (
                hseen >= WARMUP_SEEN
                and delta >= MIN_DELTA_DAYS * 86400
            ):
                exp_p = retrievability(fsrs_state, now=now)
                if fsrs_state and exp_p is not None and 0.0 < exp_p < 1.0:
                    card_before = Card.from_json(
                        _json.dumps(_state_to_card_json(fsrs_state))
                    )
                    power_preds.append(
                        float(_scheduler.get_card_retrievability(card_before, now))
                    )
                    exp_preds.append(exp_p)
                    outcomes.append(outcome)
                    streak_preds.append(
                        0.9 if last_outcome == 1
                        else 0.1 if last_outcome == 0
                        else 0.5
                    )
                    rate_preds.append(min(max(hcorr / max(hseen, 1), 0.02), 0.98))
                    if len(outcomes) >= MAX_OBSERVATIONS:
                        break

            # Update state with the actual outcome — the production binary
            # mapping: correct → Good, wrong → Again.
            card = (
                Card.from_json(_json.dumps(_state_to_card_json(fsrs_state)))
                if fsrs_state
                else Card()
            )
            updated, _log = _scheduler.review_card(
                card,
                Rating.Good if outcome == 1 else Rating.Again,
                review_datetime=now,
            )
            fsrs_state = {
                "stability": updated.stability,
                "difficulty": updated.difficulty,
                "due": updated.due.isoformat() if updated.due else None,
                "last_review": (
                    updated.last_review.isoformat() if updated.last_review else None
                ),
                "state": int(updated.state),
                "step": getattr(updated, "step", None),
            }
            last_outcome = outcome

    _REPLAY = {
        "power": power_preds,
        "exp": exp_preds,
        "outcomes": outcomes,
        "streak": streak_preds,
        "rate": rate_preds,
    }
    return _REPLAY


async def test_fsrs_calibration():
    data = _replay()
    power, exp_p, outcomes = data["power"], data["exp"], data["outcomes"]
    assert len(outcomes) >= 5000, (
        f"only {len(outcomes)} replayable observations — check the sample"
    )

    n = len(outcomes)
    mean_p = sum(outcomes) / n
    constant_preds = [mean_p] * n

    metrics = {
        "fsrs_power_auc": auc(power, outcomes),
        "exp_approx_auc": auc(exp_p, outcomes),
        "streak_auc": auc(data["streak"], outcomes),
        "rate_auc": auc(data["rate"], outcomes),
        "constant_auc": auc(constant_preds, outcomes),
        "fsrs_power_brier": brier(power, outcomes),
        "exp_approx_brier": brier(exp_p, outcomes),
        "constant_brier": brier(constant_preds, outcomes),
        "fsrs_power_logloss": log_loss(power, outcomes),
        "exp_approx_logloss": log_loss(exp_p, outcomes),
        "majority_accuracy": majority_accuracy(outcomes),
    }

    for metric, value in metrics.items():
        record(
            "fsrs", metric, case="duolingo-replay",
            score=value, threshold=None, success=None,
            reason=f"n={n} observations (delta ≥ {MIN_DELTA_DAYS}d)",
        )
    # The explicit gap the first run surfaced — kept as a finding metric.
    record(
        "fsrs", "finding_rate_vs_fsrs_auc", case="duolingo-replay",
        score=metrics["rate_auc"] - metrics["fsrs_power_auc"],
        threshold=None, success=None,
        reason=(
            "running correct-rate (item difficulty) outranks the forgetting "
            "curve on this material — fsrs defaults were fit on Anki data"
        ),
    )

    # Gates — the defensible claims:
    # 1. The power law dominates the app's exp approximation on calibration.
    assert metrics["fsrs_power_brier"] < metrics["exp_approx_brier"] - 0.10, (
        f"power-law Brier {metrics['fsrs_power_brier']:.3f} vs exp "
        f"{metrics['exp_approx_brier']:.3f} — approximation no longer detectably worse?"
    )
    assert metrics["fsrs_power_logloss"] < metrics["exp_approx_logloss"], (
        "exp approximation beats the power law on log-loss — investigate"
    )
    # 2. The scheduler ranks above chance and above the naive streak.
    assert metrics["fsrs_power_auc"] > 0.52, (
        f"FSRS AUC {metrics['fsrs_power_auc']:.3f} ≈ chance"
    )
    assert metrics["fsrs_power_auc"] > metrics["streak_auc"], (
        f"FSRS AUC {metrics['fsrs_power_auc']:.3f} does not beat last-outcome "
        f"streak ({metrics['streak_auc']:.3f})"
    )
