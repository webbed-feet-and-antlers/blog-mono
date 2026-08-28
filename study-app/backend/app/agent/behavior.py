"""Deterministic distillation of user behavior into agent memory.

The raw material is the user_activities ledger (written by the
ActivitiesLogged handler). This module folds it into two user-scope memory
keys that generation prompts, the recommendation context, and the
understanding panel all read:

  engagement     — what the learner does: per-doc views/dwell, tab
                   preferences, total action count.
  study_patterns — when and how they study: hour-of-day histogram, quiz
                   durations, study-session completion.

All logic here is deterministic (no LLM) — the narrative layer on top is
agent/reflection.py. Writers take blob_lock because both keys are
read-modify-write JSON blobs.
"""

from __future__ import annotations
from ..auth import user_ref_id

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .memory import blob_lock, get_concept_mastery, read_memory, write_memory

logger = logging.getLogger(__name__)

ENGAGEMENT_KEY = "engagement"
PATTERNS_KEY = "study_patterns"

# A single dwell longer than this is treated as an abandoned tab, not reading.
MAX_DWELL_SECS = 4 * 3600
QUIZ_HISTORY_CAP = 30
SLOW_CONCEPT_SECS = 12.0  # avg answer/review latency above this = "slow recall"

_KNOWN_TABS = ("document", "notes", "quiz", "flashcards", "concepts")


def _default_engagement() -> dict[str, Any]:
    return {
        "docs": {},  # {doc_id: {views, dwell_secs, last_viewed}}
        "total_dwell_secs": 0.0,
        "tab_switches": {tab: 0 for tab in _KNOWN_TABS},
        "actions_count": 0,
    }


def _default_study_patterns() -> dict[str, Any]:
    return {
        "hour_histogram": [0] * 24,
        "best_study_hour": None,
        "quiz_duration_history": [],  # [{secs, score}] capped
        "avg_quiz_duration_secs": None,
        "sessions": {"completed": 0, "abandoned": 0},
    }


async def get_engagement(session: AsyncSession) -> dict[str, Any]:
    val = await read_memory(session, "user", user_ref_id(), ENGAGEMENT_KEY)
    if isinstance(val, dict):
        merged = _default_engagement()
        merged.update(val)
        merged["tab_switches"] = {
            **_default_engagement()["tab_switches"],
            **(val.get("tab_switches") or {}),
        }
        return merged
    return _default_engagement()


async def get_study_patterns(session: AsyncSession) -> dict[str, Any]:
    val = await read_memory(session, "user", user_ref_id(), PATTERNS_KEY)
    if isinstance(val, dict):
        merged = _default_study_patterns()
        merged.update(val)
        hist = val.get("hour_histogram")
        if isinstance(hist, list) and len(hist) == 24:
            merged["hour_histogram"] = [int(h) for h in hist]
        return merged
    return _default_study_patterns()


def _entry_hour(ts: str) -> int | None:
    """Hour-of-day (UTC) for an activity entry's timestamp, or None."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).hour


def _doc_entry(engagement: dict, doc_id: str) -> dict:
    return engagement.setdefault("docs", {}).setdefault(
        doc_id, {"views": 0, "dwell_secs": 0.0, "last_viewed": None}
    )


async def distill_activities(session: AsyncSession, entries: list) -> None:
    """Fold a batch of activity entries into engagement + study_patterns.

    Called by the ActivitiesLogged handler. The whole read-modify-write runs
    under the blob lock so concurrent batches can't clobber each other.
    """
    if not entries:
        return

    async with blob_lock:
        engagement = await get_engagement(session)
        patterns = await get_study_patterns(session)
        now_iso = datetime.now(timezone.utc).isoformat()

        for entry in entries:
            etype = getattr(entry, "type", "") or ""
            props = getattr(entry, "props", None) or {}
            if not isinstance(props, dict):
                props = {}
            engagement["actions_count"] = int(engagement.get("actions_count", 0)) + 1

            hour = _entry_hour(getattr(entry, "ts", "") or "")
            if hour is not None:
                patterns["hour_histogram"][hour] += 1

            if etype == "document.opened":
                doc_id = str(props.get("document_id") or "")
                if doc_id:
                    doc = _doc_entry(engagement, doc_id)
                    doc["views"] = int(doc.get("views", 0)) + 1
                    doc["last_viewed"] = now_iso
            elif etype == "document.closed":
                doc_id = str(props.get("document_id") or "")
                dwell = min(float(props.get("dwell_secs") or 0), MAX_DWELL_SECS)
                if doc_id and dwell > 0:
                    doc = _doc_entry(engagement, doc_id)
                    doc["dwell_secs"] = float(doc.get("dwell_secs", 0)) + dwell
                    engagement["total_dwell_secs"] = (
                        float(engagement.get("total_dwell_secs", 0)) + dwell
                    )
            elif etype == "tab.switched":
                to = str(props.get("to") or "")
                if to in engagement["tab_switches"]:
                    engagement["tab_switches"][to] += 1
            elif etype == "study.abandoned":
                patterns["sessions"]["abandoned"] = (
                    int(patterns["sessions"].get("abandoned", 0)) + 1
                )

        # Recompute the derived hour each batch (cheap, keeps it honest).
        hist = patterns["hour_histogram"]
        patterns["best_study_hour"] = (
            max(range(24), key=lambda h: hist[h]) if any(hist) else None
        )

        await write_memory(session, "user", user_ref_id(), ENGAGEMENT_KEY, engagement)
        await write_memory(session, "user", user_ref_id(), PATTERNS_KEY, patterns)


async def record_quiz_duration(session: AsyncSession, secs: float, score: float) -> None:
    """Append a completed quiz's duration to study_patterns (blob-locked)."""
    if secs is None or secs <= 0:
        return
    async with blob_lock:
        patterns = await get_study_patterns(session)
        history = patterns.get("quiz_duration_history") or []
        history.append({"secs": round(float(secs), 1), "score": round(float(score), 3)})
        patterns["quiz_duration_history"] = history[-QUIZ_HISTORY_CAP:]
        secs_list = [h["secs"] for h in patterns["quiz_duration_history"]]
        patterns["avg_quiz_duration_secs"] = round(sum(secs_list) / len(secs_list), 1)
        await write_memory(session, "user", user_ref_id(), PATTERNS_KEY, patterns)


async def record_study_session_completed(session: AsyncSession) -> None:
    async with blob_lock:
        patterns = await get_study_patterns(session)
        patterns["sessions"]["completed"] = (
            int(patterns["sessions"].get("completed", 0)) + 1
        )
        await write_memory(session, "user", user_ref_id(), PATTERNS_KEY, patterns)


async def get_slow_concepts(
    session: AsyncSession, threshold: float = SLOW_CONCEPT_SECS, limit: int = 8
) -> list[dict[str, Any]]:
    """Concepts with the slowest average answer/review latency.

    Reads concept_mastery entries that carry a `latency` sub-dict (written
    alongside FSRS updates when the client reports per-question/per-card
    timing). Powers the understanding panel and the prompt's "slow recall"
    annotations.
    """
    mastery = await get_concept_mastery(session)
    slow: list[dict[str, Any]] = []
    for concept, entry in mastery.items():
        latency = entry.get("latency") or {}
        avg = latency.get("avg_secs")
        if avg is not None and float(avg) >= threshold:
            slow.append({
                "concept": concept,
                "avg_secs": round(float(avg), 1),
                "samples": latency.get("samples", 0),
                "mastery_pct": entry.get("mastery_pct"),
            })
    slow.sort(key=lambda c: c["avg_secs"], reverse=True)
    return slow[:limit]
