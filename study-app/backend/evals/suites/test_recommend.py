"""Recommendation suite — replay real learner interaction logs (EdNet-KT1)
through the production recommendation engine.

Per user: every question answer advances a per-concept FSRS state through
the app's own scheduler (binary mapping, same as quiz submissions), and
maintains the same per-concept mastery signals production carries —
lifetime correct-rate and the recent_rate EMA. At each simulated day
boundary we snapshot the learner's state into a UserContext and run the
REAL engine.decide() — the same strategies, boosts, and ranking the
/api/recommend endpoint uses — then score the policy two ways:

  1. Invariants: a due backlog must keep FSRS review at the top of the
     slate; the engine must never recommend nothing.
  2. Weakness precision: of the concepts the engine's primary targets,
     how many did the learner actually FAIL on their next attempt within
     7 days — versus a random-concept baseline. This is the offline proxy
     for "does the recommender point at what needs work?"

     Scored as a per-user macro average: a pooled (micro) rate lets one
     heavy user dominate the number, and the old 60-decision-point cap
     made WHICH users dominate depend on subject_id sort order. Report-only
     (not gated): on the original development cohort the engine showed zero
     lift, and on a redrawn cohort the signal inverts — the weakness
     heuristic does not generalize across cohorts (README finding #10).
"""

from __future__ import annotations

import json as _json
from datetime import datetime, timezone

import pytest

from evals.config import DATA_DIR, EVALS_SPLIT
from evals.report import record

pytestmark = pytest.mark.evals

MAX_USERS = 20
DUE_BACKLOG_MIN = 5          # invariants only apply with a real backlog
MIN_CONCEPTS_SEEN = 15       # a decision point needs a world to decide over

_REPLAY: dict | None = None


def _replay() -> dict:
    global _REPLAY
    if _REPLAY is not None:
        return _REPLAY

    import pandas as pd
    from fsrs import Card, Rating

    from app.agent.fsrs_scheduler import (
        _scheduler,
        _state_to_card_json,
        is_due,
        update_recent_rate,
    )
    from app.models import Document
    from app.recommend.context import UserContext
    from app.recommend.engine import engine
    import random as _random
    import sys as _sys

    engine_mod = _sys.modules["app.recommend.engine"]  # module, not the singleton
    strategies_mod = _sys.modules["app.recommend.strategies"]  # for the clock pin

    users_path = DATA_DIR / f"ednet_{EVALS_SPLIT}.parquet"
    questions_path = DATA_DIR / "ednet_questions.parquet"
    if not users_path.exists():
        pytest.fail(
            f"{users_path} missing — run `uv run python -m evals.data ednet`"
        )

    qdf = pd.read_parquet(questions_path)
    # question_id → its first tag = a synthetic "concept".
    qdf = qdf.drop_duplicates("question_id")
    qmap = dict(zip(qdf["question_id"], qdf["tags"].str.split(";").str[0]))

    df = pd.read_parquet(users_path, columns=[
        "subject_id", "question_id", "timestamp", "is_correct",
    ])
    df["concept"] = df["question_id"].map(qmap)
    df = df.dropna(subset=["concept"])

    rng = _random.Random(42)

    invariants_total = 0
    invariants_ok = 0
    decided_points = 0
    targeted, targeted_failed, targeted_with_next = 0, 0, 0
    random_targets, random_failed, random_with_next = 0, 0, 0
    empty_primary = 0
    # Per-user tallies — precision is macro-averaged so one heavy learner
    # can't dominate the pooled rate.
    user_stats: dict[str, dict] = {}

    for i, (subject, udf) in enumerate(df.groupby("subject_id", sort=True)):
        if i >= MAX_USERS:
            break
        u_t, u_tf, u_twn, u_rt, u_rf, u_rwn = 0, 0, 0, 0, 0, 0
        udf = udf.sort_values("timestamp")
        states: dict[str, dict] = {}     # concept → fsrs state dict
        counts: dict[str, tuple] = {}    # concept → (seen, correct)
        recent: dict[str, float] = {}    # concept → recent_rate EMA
        last_ts: dict[str, int] = {}     # concept → last attempt (epoch ms)
        last_day = None
        for row in udf.itertuples(index=False):
            ts = datetime.fromtimestamp(int(row.timestamp) / 1000, tz=timezone.utc)
            concept, correct = str(row.concept), bool(row.is_correct)
            future = udf[udf.timestamp > row.timestamp]

            day = ts.date()
            if last_day is not None and day != last_day and len(states) >= MIN_CONCEPTS_SEEN:
                # --- A decision point: snapshot + run the real engine. ---
                # `ts` is the simulated decision time — is_due is evaluated
                # against it (NOT wall-clock now: the traces are historical,
                # so real-now would mark every seen concept due).
                due_names = [c for c, st in states.items() if is_due(st, ts)]
                concepts_list = list(states.keys())
                ctx = UserContext(
                    due_concepts=[
                        {
                            "concept": c,
                            # Same signals the production get_due_concepts
                            # entries carry — the deck's failure-risk ranking
                            # and active-orbit tiering read them.
                            "fsrs": states[c],
                            "last_attempt_ts": datetime.fromtimestamp(
                                last_ts[c] / 1000, tz=timezone.utc
                            ).isoformat(),
                        }
                        for c in due_names
                    ],
                    concept_mastery={
                        c: {
                            "seen": s,
                            "correct": k,
                            "mastery_pct": round(k / s, 3),
                            # Same EMA the production mastery update
                            # maintains — the deck's failure-risk ranking
                            # (failure_risk) reads it.
                            "recent_rate": round(recent[c], 3),
                        }
                        for c, (s, k) in counts.items()
                    },
                    documents={
                        f"doc-{i}": Document(
                            id=f"doc-{i}", filename=f"topic-{i}.pdf",
                            mime="application/pdf", file_path="/tmp/x.pdf",
                            text="synthetic", kind="text",
                        )
                        for i in range(3)
                    },
                    content_by_doc={
                        f"doc-{i}": {
                            "notes": [],
                            "quiz": [],
                            "flashcards": [
                                {"id": f"d{i}-c{j}",
                                 "front": concepts_list[j],
                                 "back": concepts_list[j],
                                 "concept": concepts_list[j]}
                                for j in range(len(concepts_list))
                            ],
                        }
                        for i in range(3)
                    },
                )
                ctx.due_cards = _due_cards(ctx.content_by_doc, set(due_names))
                ctx.due_count = len(due_names)
                ctx.total_concepts = len(states)
                ctx.mastered_count = sum(
                    1 for c, (s, k) in counts.items() if s and k / s >= 0.8
                )

                # Kill epsilon-greedy randomness for reproducibility, and
                # pin the strategies' clock to the simulated decision time
                # (production ranks activity against wall-clock now; the
                # traces are historical).
                real_random = engine_mod.random.random
                engine_mod.random.random = lambda: 0.99
                real_dt = strategies_mod.datetime
                sim_now = ts

                class _sim_dt(real_dt):
                    @classmethod
                    def now(cls, tz=None):
                        return sim_now if tz is None else sim_now.astimezone(tz)

                strategies_mod.datetime = _sim_dt
                try:
                    response = engine.decide(ctx)
                finally:
                    engine_mod.random.random = real_random
                    strategies_mod.datetime = real_dt

                decided_points += 1
                primary = response["primary"]
                if primary is None:
                    empty_primary += 1
                else:
                    if len(due_names) >= DUE_BACKLOG_MIN:
                        invariants_total += 1
                        slate = [primary] + list(response["alternatives"])
                        has_review = any(
                            r["action"] == "review_flashcards" for r in slate
                        )
                        if has_review:
                            invariants_ok += 1

                    # Weakness precision: concepts the primary targets vs
                    # what the learner fails next within 7 days. with_next
                    # counts targets the learner actually faced again —
                    # the conditional metrics separate the engine's
                    # failure prediction from re-attempt availability
                    # (platform-driven, not ours).
                    targets = _primary_concepts(primary, due_names)
                    if targets:
                        for t in targets:
                            targeted += 1
                            u_t += 1
                            nxt = _next_attempt(future, qmap, t, row.timestamp,
                                                window_ms=7 * 86400 * 1000)
                            if nxt is not None:
                                targeted_with_next += 1
                                u_twn += 1
                            if nxt is False:
                                targeted_failed += 1
                                u_tf += 1
                    # Random-concept baseline over the same world.
                    pool = list(states.keys())
                    if pool:
                        r_concept = rng.choice(pool)
                        random_targets += 1
                        u_rt += 1
                        nxt = _next_attempt(future, qmap, r_concept, row.timestamp,
                                            window_ms=7 * 86400 * 1000)
                        if nxt is not None:
                            random_with_next += 1
                            u_rwn += 1
                        if nxt is False:
                            random_failed += 1
                            u_rf += 1

            # --- Advance the scheduler with this answer. ---
            st = states.get(concept)
            card = (
                Card.from_json(_json.dumps(_state_to_card_json(st)))
                if st else Card()
            )
            updated, _log = _scheduler.review_card(
                card,
                Rating.Good if correct else Rating.Again,
                review_datetime=ts,
            )
            states[concept] = {
                "stability": updated.stability,
                "difficulty": updated.difficulty,
                "due": updated.due.isoformat() if updated.due else None,
                "last_review": (updated.last_review.isoformat()
                                if updated.last_review else None),
                "state": int(updated.state),
                "step": getattr(updated, "step", None),
            }
            seen, k = counts.get(concept, (0, 0))
            counts[concept] = (seen + 1, k + (1 if correct else 0))
            recent[concept] = update_recent_rate(recent.get(concept), correct)
            last_ts[concept] = int(row.timestamp)
            last_day = day
        user_stats[str(subject)] = {
            "targeted": u_t, "targeted_failed": u_tf,
            "targeted_with_next": u_twn,
            "random_targets": u_rt, "random_failed": u_rf,
            "random_with_next": u_rwn,
        }

    _REPLAY = {
        "decided_points": decided_points,
        "invariants_total": invariants_total,
        "invariants_ok": invariants_ok,
        "empty_primary": empty_primary,
        "targeted": targeted,
        "targeted_failed": targeted_failed,
        "targeted_with_next": targeted_with_next,
        "random_targets": random_targets,
        "random_failed": random_failed,
        "random_with_next": random_with_next,
        "user_stats": user_stats,
    }
    return _REPLAY


def _due_cards(content_by_doc: dict, due_names: set[str]) -> list[dict]:
    cards = []
    for doc_id, by_type in content_by_doc.items():
        for card in by_type.get("flashcards", []):
            if card["concept"] in due_names:
                cards.append({**card, "document_id": doc_id})
    return cards


def _primary_concepts(primary: dict, due_names: list[str]) -> list[str]:
    """Concepts the primary recommendation is actually about."""
    if primary.get("action") == "review_flashcards":
        deck = primary.get("deck") or {}
        return [c["concept"] for c in deck.get("cards", [])][:5]
    target = primary.get("target") or {}
    if target.get("concepts"):
        return target["concepts"][:5]
    return []


def _next_attempt(future_df, qmap, concept, after_ms, window_ms):
    """The learner's next outcome on `concept` within the window, or None."""
    for row in future_df.itertuples(index=False):
        if int(row.timestamp) - after_ms > window_ms:
            return None
        if qmap.get(row.question_id) == concept:
            return bool(row.is_correct)
    return None


async def test_recommend_policy_on_real_traces():
    data = _replay()
    assert data["decided_points"] >= 20, (
        f"only {data['decided_points']} decision points replayed"
    )

    # 1. The engine always recommends something.
    empty_rate = data["empty_primary"] / data["decided_points"]
    record(
        "recommend", "empty_primary_rate", case="ednet-replay",
        score=empty_rate, threshold=0.05, success=empty_rate <= 0.05,
        reason=f"{data['empty_primary']}/{data['decided_points']} points",
    )
    assert empty_rate <= 0.05, "engine returned no primary at decision points"

    # 2. Due backlog keeps FSRS review on the slate.
    if data["invariants_total"]:
        slate_ok = data["invariants_ok"] / data["invariants_total"]
        record(
            "recommend", "due_backlog_top_slate", case="ednet-replay",
            score=slate_ok, threshold=0.90, success=slate_ok >= 0.90,
            reason=f"{data['invariants_ok']}/{data['invariants_total']} backlogs",
        )
        assert slate_ok >= 0.90, (
            f"FSRS review missing from slate in {1 - slate_ok:.0%} of backlogs"
        )

    # 3. Weakness precision vs random — per-user macro average, report-only.
    # Gating this was wrong twice over: the pooled rate let one heavy user
    # dominate, and the "never worse than random" claim itself turned out
    # to be cohort-specific (zero lift on the development cohort, inverted
    # on a redrawn one — README finding #10).
    stats = data["user_stats"]
    eng_users = [v for v in stats.values() if v["targeted"] > 0]
    rnd_users = [v for v in stats.values() if v["random_targets"] > 0]
    eng_prec = (
        sum(v["targeted_failed"] / v["targeted"] for v in eng_users) / len(eng_users)
        if eng_users else None
    )
    rnd_prec = (
        sum(v["random_failed"] / v["random_targets"] for v in rnd_users)
        / len(rnd_users)
        if rnd_users else None
    )
    if eng_prec is not None and rnd_prec is not None:
        lift = eng_prec - rnd_prec
        record(
            "recommend", "engine_target_precision", case="ednet-replay",
            score=eng_prec, threshold=None, success=None,
            reason=(
                f"macro over {len(eng_users)} users "
                f"(pooled {data['targeted_failed']}/{data['targeted']})"
            ),
        )
        record(
            "recommend", "random_target_precision", case="ednet-replay",
            score=rnd_prec, threshold=None, success=None,
            reason=(
                f"macro over {len(rnd_users)} users "
                f"(pooled {data['random_failed']}/{data['random_targets']})"
            ),
        )
        record(
            "recommend", "weakness_precision_lift", case="ednet-replay",
            score=lift, threshold=None, success=None,
            reason=(
                "engine-targeted vs random-targeted failure precision "
                "(macro); cohort-dependent — do not tune against"
            ),
        )

    # 4. Conditional view (report-only): precision among targets the
    # learner actually faced again, plus how often that happened. Raw
    # precision conflates failure prediction (the engine's job) with
    # re-attempt availability (platform-driven); these split the two.
    eng_wn_users = [v for v in stats.values() if v["targeted_with_next"] > 0]
    rnd_wn_users = [v for v in stats.values() if v["random_with_next"] > 0]
    if eng_users and eng_wn_users:
        eng_cond = (
            sum(v["targeted_failed"] / v["targeted_with_next"]
                for v in eng_wn_users) / len(eng_wn_users)
        )
        eng_reattempt = (
            sum(v["targeted_with_next"] / v["targeted"]
                for v in eng_users) / len(eng_users)
        )
        record(
            "recommend", "engine_precision_conditioned", case="ednet-replay",
            score=eng_cond, threshold=None, success=None,
            reason=(
                f"failures among re-attempted targets "
                f"(macro over {len(eng_wn_users)} users)"
            ),
        )
        record(
            "recommend", "engine_reattempt_fraction", case="ednet-replay",
            score=eng_reattempt, threshold=None, success=None,
            reason="fraction of engine targets faced again within 7d (macro)",
        )
    if rnd_wn_users:
        rnd_cond = (
            sum(v["random_failed"] / v["random_with_next"]
                for v in rnd_wn_users) / len(rnd_wn_users)
        )
        record(
            "recommend", "random_precision_conditioned", case="ednet-replay",
            score=rnd_cond, threshold=None, success=None,
            reason=(
                f"failures among re-attempted random draws "
                f"(macro over {len(rnd_wn_users)} users)"
            ),
        )
