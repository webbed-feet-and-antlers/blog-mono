"""FSRS calibration suite — replay real forgetting-curve outcomes through
the production scheduler (Duolingo's public 13M-trace HLR dataset).

The question a scheduler exists to answer: *given everything observed so
far, what is the probability the learner recalls this item right now?*
For each (user, lexeme) trace we replay every prior practice session through
the app's real FSRS code path (binary rating mapping: correct → Good, wrong
→ Again, exactly what quiz/flashcard submissions do), then predict the
current session's recall two ways from the SAME scheduler state:

  - the fsrs library's native power-law forgetting curve, and
  - the app's `retrievability` wrapper (which now delegates to that same
    curve — gated below so it can never silently drift back apart).

Observations are restricted to sessions at least MIN_DELTA_DAYS after the
previous one — the regime where a forgetting curve is the operative model
(59% of Duolingo sessions are same-day practice where R≈1 for every item
and ranking is pure noise).

What the first runs of this suite established (recorded as report-only
metrics so the numbers stay visible):

  1. FIXED (2026-08): the wrapper's exp(-t/S) approximation was
     catastrophically miscalibrated (Brier 0.3-0.6 vs the power-law's
     ~0.08). `retrievability` now delegates to the library's power law.
     Gated: the wrapper must match the library curve AND clear absolute
     calibration bars (Brier ≤ 0.12, log-loss ≤ 0.50) — so neither a
     regression to a hand-rolled formula nor silent drift can return.
  2. FSRS with default parameters does NOT beat the running correct-rate
     baseline (item difficulty): on this material, what the learner got
     right before predicts the next session better than time-decay does,
     and the last-outcome streak sits within sampling noise of the curve
     (|gap| ≤ 0.006 across draws). The fsrs parameters were fit on Anki
     data; Duolingo vocabulary is easier material with a ~93% base
     success rate. Recorded as findings; only a collapse floor is gated.
"""

from __future__ import annotations

import json as _json
from datetime import datetime, timezone

import pytest

from evals.config import DATA_DIR, EVALS_SPLIT
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

    path = DATA_DIR / f"duolingo_{EVALS_SPLIT}.parquet"
    if not path.exists():
        pytest.fail(
            f"{path} missing — run `uv run python -m evals.data duolingo`"
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
    prod_preds: list[float] = []
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
                prod_p = retrievability(fsrs_state, now=now)
                if fsrs_state and prod_p is not None and 0.0 < prod_p < 1.0:
                    card_before = Card.from_json(
                        _json.dumps(_state_to_card_json(fsrs_state))
                    )
                    power_preds.append(
                        float(_scheduler.get_card_retrievability(card_before, now))
                    )
                    prod_preds.append(prod_p)
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
        "prod": prod_preds,
        "outcomes": outcomes,
        "streak": streak_preds,
        "rate": rate_preds,
    }
    return _REPLAY


def _train_baseline() -> float:
    """Mean recall over TRAIN users' scorable sessions — the constant
    predictor's probability, estimated out-of-sample."""
    import pandas as pd

    path = DATA_DIR / "duolingo_train.parquet"
    if not path.exists():
        pytest.fail(
            f"{path} missing — run `uv run python -m evals.data duolingo`"
        )
    df = pd.read_parquet(
        path, columns=["p_recall", "delta", "history_seen"]
    )
    mask = (df["history_seen"] >= WARMUP_SEEN) & (
        df["delta"] >= MIN_DELTA_DAYS * 86400
    )
    df = df[mask]
    assert len(df) > 0, "train split has no scorable sessions"
    return float((df["p_recall"] >= 0.5).mean())


async def test_fsrs_calibration():
    data = _replay()
    power, prod, outcomes = data["power"], data["prod"], data["outcomes"]
    assert len(outcomes) >= 500, (
        f"only {len(outcomes)} replayable observations — check the sample "
        "(per split expect ~800-1500: only ~26% of scorable sessions carry "
        "a warm FSRS state with a valid pre-update retrievability)"
    )

    n = len(outcomes)
    # Constant baseline estimated on TRAIN users only — the honest version of
    # the running-mean predictor (the old code computed it on its own eval
    # set, which was in-sample for this baseline).
    mean_p = _train_baseline()
    constant_preds = [mean_p] * n

    metrics = {
        "fsrs_power_auc": auc(power, outcomes),
        "retrievability_auc": auc(prod, outcomes),
        "streak_auc": auc(data["streak"], outcomes),
        "rate_auc": auc(data["rate"], outcomes),
        "constant_auc": auc(constant_preds, outcomes),
        "fsrs_power_brier": brier(power, outcomes),
        "retrievability_brier": brier(prod, outcomes),
        "constant_brier": brier(constant_preds, outcomes),
        "fsrs_power_logloss": log_loss(power, outcomes),
        "retrievability_logloss": log_loss(prod, outcomes),
        "majority_accuracy": majority_accuracy(outcomes),
    }

    for metric, value in metrics.items():
        record(
            "fsrs", metric, case="duolingo-replay",
            score=value, threshold=None, success=None,
            reason=f"n={n} observations (delta ≥ {MIN_DELTA_DAYS}d)",
        )
    # The explicit gaps the first run surfaced — kept as finding metrics.
    record(
        "fsrs", "finding_rate_vs_fsrs_auc", case="duolingo-replay",
        score=metrics["rate_auc"] - metrics["fsrs_power_auc"],
        threshold=None, success=None,
        reason=(
            "running correct-rate (item difficulty) outranks the forgetting "
            "curve on this material — fsrs defaults were fit on Anki data"
        ),
    )
    record(
        "fsrs", "finding_streak_vs_fsrs_auc", case="duolingo-replay",
        score=metrics["streak_auc"] - metrics["fsrs_power_auc"],
        threshold=None, success=None,
        reason=(
            "last-outcome streak vs the forgetting curve — within sampling "
            "noise on every draw so far (|gap| ≤ 0.006 at n≈1k, AUC SE "
            "≈0.02); not a defensible gate either direction"
        ),
    )

    # Gates — the defensible claims:
    # 1. The wrapper must return the library's curve (the exp(-t/S) era is
    #    over; this guards against a hand-rolled formula returning).
    assert (
        abs(metrics["retrievability_brier"] - metrics["fsrs_power_brier"]) <= 0.02
    ), (
        f"retrievability Brier {metrics['retrievability_brier']:.3f} drifted "
        f"from the library power law ({metrics['fsrs_power_brier']:.3f})"
    )
    # 2. Calibrated as a probability, absolutely (the fixed formula lands
    #    at Brier ~0.08 / log-loss ~0.31 on this replay).
    assert metrics["retrievability_brier"] <= 0.12, (
        f"retrievability Brier {metrics['retrievability_brier']:.3f} is "
        "not a usable recall probability"
    )
    assert metrics["retrievability_logloss"] <= 0.50, (
        f"retrievability log-loss {metrics['retrievability_logloss']:.3f} is "
        "not a usable recall probability"
    )
    # 3. Ranking sanity floor. Superiority claims over the streak baseline
    #    are within sampling noise on this data (see finding metrics above);
    #    the gate's job is catching collapse — a curve that no longer ranks
    #    better than near-chance is broken.
    assert metrics["retrievability_auc"] > 0.51, (
        f"FSRS AUC {metrics['retrievability_auc']:.3f} collapsed to chance"
    )
