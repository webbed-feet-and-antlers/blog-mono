"""Windowed usage statistics — one source of truth for user-facing
analytics and agent-facing context.

Everything here is plain Python aggregation over rows fetched in the
window (the activity ledger and quiz attempts are small by design), which
keeps it dialect-portable: no date_trunc/strftime, identical results on
SQLite and Postgres. Both the /api/analytics route and the agent's
build_context consume the same numbers, so the dashboard can never drift
from what the planner sees.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import user_ref_id
from ..models import ContentItem, QuizAttempt, UserActivity
from . import memory as memory_store
from .fsrs_scheduler import retrievability

# Concepts need a few observations before their recent_rate means anything.
RETENTION_MIN_SEEN = 3
RETENTION_BUCKETS = 5


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def window_summary(
    session: AsyncSession, user_id: str | None = None, days: int = 7
) -> dict[str, Any]:
    """Aggregate the user's study activity over the last `days` days.

    Shape:
      study:    minutes_by_day, total_minutes, active_days, streak_days
      quizzes:  count, avg_score, score_by_day, questions_answered,
                avg_question_latency_secs
      concepts: per-concept accuracy in the window, weakest first
      retention_curve: observed recall (recent_rate) vs FSRS-predicted
                retrievability, bucketed — the user's personal forgetting curve
      top_activities: ledger event-type histogram
    """
    uid = user_id if user_id is not None else user_ref_id()
    days = max(1, min(days, 90))
    cutoff = _utcnow() - timedelta(days=days)

    activities = (
        (
            await session.execute(
                select(UserActivity.ts, UserActivity.type, UserActivity.props).where(
                    UserActivity.user_id == uid, UserActivity.ts >= cutoff
                )
            )
        ).all()
    )
    attempts = (
        (
            await session.execute(
                select(QuizAttempt)
                .options(selectinload(QuizAttempt.content_item))
                .where(QuizAttempt.user_id == uid, QuizAttempt.taken_at >= cutoff)
            )
        )
        .scalars()
        .all()
    )

    return {
        "days": days,
        "window_start": cutoff.isoformat(),
        "study": _study_time(activities, days),
        "quizzes": _quiz_stats(attempts, activities),
        "concepts": await _concept_accuracy(attempts),
        "retention_curve": await _retention_curve(session),
        "top_activities": _activity_histogram(activities),
    }


def _study_time(activities: list, days: int) -> dict[str, Any]:
    """Daily study minutes from document dwell events.

    document.closed carries dwell_secs (the frontend tracks open→close);
    days with no dwell events contribute zero so the chart has no holes.
    """
    by_day: dict[str, float] = {}
    for row in activities:
        ts, typ, props = row[0], row[1], row[2]
        if typ != "document.closed":
            continue
        dwell = (props or {}).get("dwell_secs")
        if not isinstance(dwell, (int, float)) or dwell <= 0:
            continue
        day = ts.date().isoformat()
        by_day[day] = by_day.get(day, 0.0) + float(dwell) / 60.0

    today = _utcnow().date()
    minutes_by_day = []
    for i in range(days - 1, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        minutes_by_day.append({"date": day, "minutes": round(by_day.get(day, 0.0), 1)})

    active_days = sum(1 for d in minutes_by_day if d["minutes"] > 0)
    # Streak = consecutive active days ending today (or yesterday, so a
    # morning session doesn't read as broken later the same night).
    ordered = [d["minutes"] > 0 for d in reversed(minutes_by_day)]
    streak = 0
    if ordered and not ordered[0]:
        ordered = ordered[1:]
    for is_active in ordered:
        if not is_active:
            break
        streak += 1
    return {
        "minutes_by_day": minutes_by_day,
        "total_minutes": round(sum(d["minutes"] for d in minutes_by_day), 1),
        "active_days": active_days,
        "streak_days": streak,
    }


def _quiz_stats(attempts: list[QuizAttempt], activities: list) -> dict[str, Any]:
    """Quiz outcomes + answer latency.

    Per-question latency comes from quiz.answered ledger events (the
    frontend timestamps each answer); avg latency × questions approximates
    how long a quiz takes without the client tracking quiz-level timers.
    """
    scores_by_day: dict[str, list[float]] = {}
    for a in attempts:
        day = a.taken_at.date().isoformat()
        scores_by_day.setdefault(day, []).append(a.score)

    latencies = []
    for row in activities:
        ts, typ, props = row[0], row[1], row[2]
        if typ != "quiz.answered":
            continue
        secs = (props or {}).get("latency_secs")
        if isinstance(secs, (int, float)) and secs > 0:
            latencies.append(float(secs))

    return {
        "count": len(attempts),
        "avg_score": (
            round(sum(a.score for a in attempts) / len(attempts), 3) if attempts else None
        ),
        "score_by_day": [
            {"date": day, "avg_score": round(sum(s) / len(s), 3), "count": len(s)}
            for day, s in sorted(scores_by_day.items())
        ],
        "questions_answered": len(latencies),
        "avg_question_latency_secs": (
            round(sum(latencies) / len(latencies), 1) if latencies else None
        ),
    }


async def _concept_accuracy(attempts: list[QuizAttempt]) -> list[dict[str, Any]]:
    """Per-concept correctness within the window, weakest first.

    Maps each attempt's answers onto its quiz's questions (which carry
    concept tags) — a true time-scoped accuracy, unlike the lifetime
    mastery_pct in the memory store.
    """
    per_concept: dict[str, dict[str, int]] = {}
    for attempt in attempts:
        questions = (attempt.content_item.content or {}).get("questions", [])
        by_qid = {q.get("id"): q for q in questions if isinstance(q, dict)}
        for qid, selected in (attempt.answers or {}).items():
            q = by_qid.get(qid)
            if q is None:
                continue
            concept = (q.get("concept") or "").strip()
            if not concept:
                continue
            entry = per_concept.setdefault(concept, {"correct": 0, "total": 0})
            entry["total"] += 1
            if selected == q.get("answer_idx"):
                entry["correct"] += 1
    result = [
        {
            "concept": c,
            "correct": e["correct"],
            "total": e["total"],
            "accuracy": round(e["correct"] / e["total"], 3),
        }
        for c, e in per_concept.items()
    ]
    result.sort(key=lambda e: (e["accuracy"], -e["total"]))
    return result


async def _retention_curve(session: AsyncSession) -> list[dict[str, Any]]:
    """Observed recall vs FSRS-predicted retrievability.

    For every concept with enough observations, predicted = the FSRS
    power-law retrievability right now, observed = recent_rate (EMA of
    recent outcomes). Bucketing predicted R into equal-width bins gives a
    personal calibration curve: if observation tracks prediction, the
    memory model fits this learner.
    """
    mastery = await memory_store.get_concept_mastery(session)
    buckets: list[dict[str, Any]] = [
        {"lo": i / RETENTION_BUCKETS, "hi": (i + 1) / RETENTION_BUCKETS, "sum": 0.0, "n": 0}
        for i in range(RETENTION_BUCKETS)
    ]
    for entry in mastery.values():
        if entry.get("seen", 0) < RETENTION_MIN_SEEN:
            continue
        observed = entry.get("recent_rate")
        predicted = retrievability(entry.get("fsrs"))
        if observed is None or predicted is None:
            continue
        idx = min(int(predicted * RETENTION_BUCKETS), RETENTION_BUCKETS - 1)
        buckets[idx]["sum"] += float(observed)
        buckets[idx]["n"] += 1
    return [
        {
            "bucket_lo": round(b["lo"], 2),
            "bucket_hi": round(b["hi"], 2),
            "predicted": round((b["lo"] + b["hi"]) / 2, 2),
            "observed": round(b["sum"] / b["n"], 3) if b["n"] else None,
            "n": b["n"],
        }
        for b in buckets
    ]


def _activity_histogram(activities: list, limit: int = 12) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in activities:
        counts[row[1]] = counts.get(row[1], 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    return [{"type": t, "count": c} for t, c in ranked]
