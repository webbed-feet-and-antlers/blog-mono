"""Learner reflection — the LLM narrative layer over behavioral memory.

Everything else that models the learner is deterministic (quiz scores →
level, cadence → goal, actions → histograms). This module is where the
agent actually *reads* the accumulated behavior and writes back an
understanding: a grounded summary, a handful of traits, a habits line.

Grounding rules:
  - The prompt contains only distilled facts (stats, histograms, top docs,
    weakest/strongest/slowest concepts, activity-type counts).
  - The system prompt forbids invention; every claim must trace to the data.
  - The output is structured JSON {summary, traits, habits}.

Cooldown: reflection costs an LLM call, so it only runs when there is
enough NEW behavior since last time (or when forced by the user via
POST /api/memory/reflect — the understanding panel's refresh button).

Triggers: the proactive loop tick + the manual endpoint. No new background
worker needed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..llm import chat_json
from ..models import UserActivity
from . import behavior as behavior_store
from . import memory as memory_store

logger = logging.getLogger(__name__)

INSIGHTS_KEY = "learner_insights"

# Reflect only when this many new ledger rows accumulated since last time…
MIN_NEW_ACTIVITIES = 25
# …and at least this long has passed (an hour), unless forced.
MIN_INTERVAL_SECS = 3600


async def get_learner_insights(session: AsyncSession) -> dict[str, Any] | None:
    val = await memory_store.read_memory(session, "user", "", INSIGHTS_KEY)
    return val if isinstance(val, dict) else None


async def reflect_on_learner(
    session: AsyncSession, force: bool = False
) -> dict[str, Any]:
    """Synthesize the learner model from behavior. Returns a status dict:

    {"status": "updated"|"skipped", "reason": ..., "insights": ...?}
    Never raises — a failed reflection keeps the previous insights.
    """
    total_activities = await session.scalar(select(func.count(UserActivity.id)))
    total_activities = int(total_activities or 0)

    if not force:
        verdict = await _should_skip(session, total_activities)
        if verdict is not None:
            return {"status": "skipped", "reason": verdict}

    if total_activities == 0:
        return {"status": "skipped", "reason": "no activities recorded yet"}

    packet = await _build_grounding_packet(session, total_activities)
    if not packet and not force:
        return {"status": "skipped", "reason": "not enough signal to reflect on"}

    try:
        result = await chat_json(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Analyze this student's usage data and return the JSON:\n\n"
                        + _render_packet(packet)
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=700,
        )
    except Exception as exc:
        logger.exception("[reflection] LLM call failed")
        return {"status": "error", "reason": str(exc)[:300]}

    summary = str(result.get("summary") or "").strip()
    traits = result.get("traits") or []
    habits = str(result.get("habits") or "").strip()
    if not summary or not isinstance(traits, list):
        return {"status": "error", "reason": "LLM returned no summary/traits"}

    insights = {
        "summary": summary,
        "traits": [str(t)[:80] for t in traits[:6] if str(t).strip()],
        "habits": habits[:300],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "activities_seen": total_activities,
    }
    async with memory_store.blob_lock:
        await memory_store.write_memory(
            session, "user", "", INSIGHTS_KEY, insights
        )
    logger.info(
        "[reflection] learner insights updated (%d traits, from %d activities)",
        len(insights["traits"]),
        total_activities,
    )
    return {"status": "updated", "insights": insights}


async def _should_skip(session: AsyncSession, total_activities: int) -> str | None:
    prev = await get_learner_insights(session)
    if prev is None:
        return None  # first reflection — go
    seen = int(prev.get("activities_seen") or 0)
    if total_activities - seen < MIN_NEW_ACTIVITIES:
        return (
            f"only {total_activities - seen} new activities "
            f"(need {MIN_NEW_ACTIVITIES})"
        )
    updated_at = str(prev.get("updated_at") or "")
    try:
        last = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        if elapsed < MIN_INTERVAL_SECS:
            return f"last reflection {int(elapsed // 60)}m ago (min 1h)"
    except ValueError:
        pass
    return None


async def _build_grounding_packet(
    session: AsyncSession, total_activities: int
) -> dict[str, Any]:
    """Collect every deterministic signal into one compact, prompt-ready dict."""
    profile = await memory_store.get_learner_profile(session)
    patterns = await behavior_store.get_study_patterns(session)
    engagement = await behavior_store.get_engagement(session)
    mastery = await memory_store.get_concept_mastery(session)
    weak = await memory_store.get_weak_topics(session)

    # Recent activity-type mix.
    rows = await session.execute(
        select(UserActivity.type, func.count(UserActivity.id))
        .group_by(UserActivity.type)
        .order_by(func.count(UserActivity.id).desc())
        .limit(12)
    )
    activity_types = {t: c for t, c in rows.all()}

    # Weakest / strongest tested concepts (by mastery_pct, need >= 2 seen).
    tested = [
        (name, e)
        for name, e in mastery.items()
        if e.get("seen", 0) >= 2 and e.get("mastery_pct") is not None
    ]
    tested.sort(key=lambda kv: kv[1]["mastery_pct"])
    weakest = [name for name, _ in tested[:10]]
    strongest = [name for name, _ in tested[-5:][::-1] if name not in weakest]

    slow = await behavior_store.get_slow_concepts(session, limit=6)

    # Top documents by dwell.
    docs = engagement.get("docs") or {}
    top_docs = sorted(
        docs.items(), key=lambda kv: kv[1].get("dwell_secs", 0), reverse=True
    )[:5]

    hist = patterns.get("hour_histogram") or [0] * 24
    return {
        "profile": {
            "level": profile.get("learner_level"),
            "preferred_difficulty": profile.get("preferred_difficulty"),
            "study_goal": profile.get("study_goal"),
            "stats": profile.get("stats"),
        },
        "activity_totals": total_activities,
        "recent_activity_types": activity_types,
        "study_patterns": {
            "hour_histogram_utc": hist,
            "best_study_hour_utc": patterns.get("best_study_hour"),
            "avg_quiz_duration_secs": patterns.get("avg_quiz_duration_secs"),
            "sessions": patterns.get("sessions"),
        },
        "engagement": {
            "total_dwell_secs": round(engagement.get("total_dwell_secs", 0)),
            "tab_switches": engagement.get("tab_switches"),
            "top_documents_by_dwell": [
                {"doc_id": d, "dwell_secs": round(v.get("dwell_secs", 0))}
                for d, v in top_docs
                if v.get("dwell_secs", 0) > 0
            ],
        },
        "weakest_concepts": weakest,
        "strongest_concepts": strongest,
        "slow_recall_concepts": [
            {"concept": c["concept"], "avg_secs": c["avg_secs"]} for c in slow
        ],
        "weak_topics": [w.get("topic") for w in weak[:10]],
    }


def _render_packet(packet: dict[str, Any]) -> str:
    import json

    return json.dumps(packet, default=str, indent=1)


_SYSTEM_PROMPT = (
    "You analyze a student's study-app usage data to build a short, honest "
    "profile of them as a learner. You are given distilled signals: profile "
    "stats, activity-type counts, an hour-of-day histogram (UTC), document "
    "engagement, and concept-level mastery/latency.\n\n"
    "RULES:\n"
    "- Write entirely in English.\n"
    "- Base EVERY claim on the provided data. Never invent, guess, or "
    "generalize beyond the evidence. If a signal is empty or 'unknown', "
    "say nothing about it.\n"
    "- Be specific and behavioral (e.g. 'reviews flashcards more than "
    "quizzes', 'active in evening hours', 'slow recall on X') rather than "
    "vague praise.\n"
    "- Keep it kind and useful — this is shown to the student.\n"
    "- Return ONLY compact JSON — no markdown fences, no commentary.\n\n"
    "Return ONLY JSON:\n"
    '{"summary": "2-3 sentences describing this learner based on the data", '
    '"traits": ["3-6 short trait strings, each evidence-based"], '
    '"habits": "1 sentence on when/how they study"}'
)
