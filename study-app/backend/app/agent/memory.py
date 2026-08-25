"""Agent memory read/write helpers backed by the agent_memory table.

Scope:
  - "doc":  ref_id = document_id. Caches analysis, extracted concepts, prior generations.
  - "user": ref_id = "" (single local user for the POC). Cross-document learnings.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AgentMemory

# Serializes read-modify-write cycles on the user-scope JSON blobs
# (concept_mastery, weak_topics, learner_profile, session). Each blob is one
# row rewritten wholesale, so two concurrent requests (e.g. overlapping quiz
# submits) would otherwise clobber each other's writes. Single-process app —
# an asyncio.Lock is sufficient.
blob_lock = asyncio.Lock()


async def read_memory(
    session: AsyncSession, scope: str, ref_id: str, key: str
) -> Any | None:
    """Return a single memory value, or None if absent."""
    result = await session.execute(
        select(AgentMemory.value).where(
            AgentMemory.scope == scope,
            AgentMemory.ref_id == ref_id,
            AgentMemory.key == key,
        )
    )
    row = result.first()
    return row[0] if row is not None else None


async def read_memory_scope(
    session: AsyncSession, scope: str, ref_id: str
) -> dict[str, Any]:
    """Return all key/value entries for a given scope+ref as a dict."""
    result = await session.execute(
        select(AgentMemory.key, AgentMemory.value).where(
            AgentMemory.scope == scope,
            AgentMemory.ref_id == ref_id,
        )
    )
    return {k: v for k, v in result.all()}


async def write_memory(
    session: AsyncSession, scope: str, ref_id: str, key: str, value: Any
) -> None:
    """Upsert a memory entry (SQLite ON CONFLICT update)."""
    stmt = sqlite_insert(AgentMemory).values(
        id=uuid.uuid4().hex[:12],
        scope=scope,
        ref_id=ref_id,
        key=key,
        value=value,
    )
    # On conflict (same scope+ref_id+key), update value + updated_at.
    update_cols = {
        "value": stmt.excluded.value,
    }
    from datetime import datetime, timezone

    update_cols["updated_at"] = datetime.now(timezone.utc)
    stmt = stmt.on_conflict_do_update(
        index_elements=[AgentMemory.scope, AgentMemory.ref_id, AgentMemory.key],
        set_=update_cols,
    )
    await session.execute(stmt)


async def list_memory(
    session: AsyncSession, scope: str | None = None, ref_id: str | None = None
) -> list[AgentMemory]:
    """List memory rows (optionally filtered) — used by the debug endpoint."""
    stmt = select(AgentMemory)
    if scope is not None:
        stmt = stmt.where(AgentMemory.scope == scope)
    if ref_id is not None:
        stmt = stmt.where(AgentMemory.ref_id == ref_id)
    stmt = stmt.order_by(AgentMemory.updated_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_doc_topics(session: AsyncSession) -> dict[str, str]:
    """Return {document_id: topic} from every cached doc analysis.

    One query for all docs — used by the module-tree and concept-reference
    endpoints to subtitle documents with their analysis topic.
    """
    rows = await list_memory(session, scope="doc")
    topics: dict[str, str] = {}
    for m in rows:
        if m.key == "analysis" and isinstance(m.value, dict):
            topic = m.value.get("topic")
            if topic:
                topics[m.ref_id] = str(topic)
    return topics


# --- Weak-topic tracking (the proactive agent's signal source) -------------
#
# weak_topics lives at scope="user", ref_id="", key="weak_topics" and is a
# list of {"topic": str, "missed_count": int, "last_seen": iso8601} dicts.
# missed_count accumulates across quizzes; last_seen marks recency. The list
# is capped at MAX_WEAK_TOPICS entries, sorted by missed_count desc so the
# weakest topics rank first.

MAX_WEAK_TOPICS = 20


async def add_weak_topics(session: AsyncSession, topics: list[str]) -> None:
    """Merge missed concepts into the user-wide weak_topics memory.

    Existing entries for the same topic have their missed_count incremented
    and last_seen refreshed; new topics are appended. The list is re-sorted
    by missed_count (descending) and capped.
    """
    if not topics:
        return

    now = datetime.now(timezone.utc).isoformat()
    async with blob_lock:
        existing = await read_memory(session, "user", "", "weak_topics")
        by_topic: dict[str, dict[str, Any]] = {}
        if isinstance(existing, list):
            for entry in existing:
                t = entry.get("topic")
                if t:
                    by_topic[t] = dict(entry)

        for topic in topics:
            if topic in by_topic:
                by_topic[topic]["missed_count"] = int(by_topic[topic].get("missed_count", 0)) + 1
                by_topic[topic]["last_seen"] = now
            else:
                by_topic[topic] = {"topic": topic, "missed_count": 1, "last_seen": now}

        merged = sorted(by_topic.values(), key=lambda e: e.get("missed_count", 0), reverse=True)
        await write_memory(session, "user", "", "weak_topics", merged[:MAX_WEAK_TOPICS])


async def get_weak_topics(session: AsyncSession) -> list[dict[str, Any]]:
    """Return the user-wide weak-topics list (empty if none recorded)."""
    val = await read_memory(session, "user", "", "weak_topics")
    if isinstance(val, list):
        return val
    return []


async def get_review_candidates(
    session: AsyncSession, cooldown_hours: int = 24
) -> list[dict[str, Any]]:
    """Find documents that need a proactive review deck.

    A document qualifies when:
      - it has been analyzed (analysis cached in doc memory) so we know its
        concepts, AND
      - the learner has weak topics recorded for it (cross-referenced), AND
      - no proactive flashcard deck was generated for it within `cooldown_hours`.

    Returns a list of {document_id, weak_topics: [str], document_text: str}
    ready to feed into run_generation().
    """
    from datetime import timedelta
    from ..models import ContentItem, Document  # local import avoids cycles

    weak = await get_weak_topics(session)
    weak_names = {w["topic"] for w in weak} if weak else set()

    # FSRS due concepts — the scientifically-scheduled review trigger.
    all_due = await get_due_concepts(session)
    due_names = {d["concept"] for d in all_due} if all_due else set()

    # Need at least one signal to act on.
    if not weak_names and not due_names:
        return []

    cooldown_cutoff = datetime.now(timezone.utc) - timedelta(hours=cooldown_hours)

    # Load all docs that have an analysis (only those are useful targets).
    analyses = await list_memory(session, scope="doc")
    doc_ids_with_analysis: dict[str, dict[str, Any]] = {}
    for m in analyses:
        if m.key == "analysis":
            doc_ids_with_analysis[m.ref_id] = m.value or {}

    candidates: list[dict[str, Any]] = []
    for doc_id, analysis in doc_ids_with_analysis.items():
        doc_concepts = {str(c) for c in (analysis.get("concepts") or [])}
        # Union of weak topics AND FSRS-due concepts — either is a reason to
        # proactively generate a review deck for this document.
        relevant = sorted((weak_names | due_names) & doc_concepts)
        if not relevant:
            continue

        # Dedup: skip if a proactive deck was generated for this doc recently.
        recent = await session.execute(
            select(ContentItem).where(
                ContentItem.document_id == doc_id,
                ContentItem.type == "flashcards",
                ContentItem.created_at >= cooldown_cutoff,
            )
        )
        has_recent = any(
            (row[0].content.get("origin") == "proactive") if hasattr(row[0], "content") else False
            for row in recent.all()
        )
        if has_recent:
            continue

        doc = await session.get(Document, doc_id)
        if doc is None:
            continue

        candidates.append({
            "document_id": doc_id,
            "weak_topics": relevant,
            "document_text": doc.text,
        })
    return candidates


def _as_jsonable(value: Any) -> Any:
    """Best-effort coercion to JSON-native types for storage."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return {"raw": str(value)}


# --- Per-concept mastery (the interaction-tracking signal) -----------------
#
# concept_mastery lives at scope="user", ref_id="", key="concept_mastery" and
# is a dict keyed by concept name:
#   {concept: {"correct": int, "wrong": int, "seen": int, "mastery_pct": float}}
#
# Both quiz answers (right AND wrong) and flashcard reviews ("I know this" /
# "Still learning") feed this tally. It's always-on (not gated like the
# weak-topics feedback loop) because we want the full picture — mastery
# reflects correct answers too, not just misses. retrieve_memory passes this
# to generation so the agent can weight toward low-mastery concepts.


async def update_concept_mastery(
    session: AsyncSession,
    concept: str,
    correct: bool,
    rating: int | None = None,
    latency_secs: float | None = None,
) -> None:
    """Record one interaction outcome for a concept.

    Increments seen + (correct|wrong), recomputes mastery_pct, and updates
    the concept's FSRS spaced-repetition state (stability, difficulty, due
    date). Concept mastery is cross-document (user scope) so mastering a
    concept in one doc carries over. Skips empty/whitespace concept strings.

    Args:
        correct: Whether the learner got it right (quiz/flashcard).
        rating: FSRS rating (1=Again, 2=Hard, 3=Good, 4=Easy). If None,
                inferred from `correct`: Good if correct, Again if wrong.
        latency_secs: How long the learner took to answer/review, when the
                client reports it. Rolled into entry["latency"] as a running
                average — slow+wrong is a stronger weakness signal than
                either alone.
    """
    concept = (concept or "").strip()
    if not concept:
        return

    # Infer FSRS rating from correctness if not explicitly provided.
    if rating is None:
        rating = 3 if correct else 1  # Good / Again

    from . import fsrs_scheduler

    async with blob_lock:
        mastery = await get_concept_mastery(session)
        entry = mastery.get(concept) or {"correct": 0, "wrong": 0, "seen": 0}
        entry["seen"] = entry["seen"] + 1
        if correct:
            entry["correct"] = entry["correct"] + 1
        else:
            entry["wrong"] = entry["wrong"] + 1
        entry["mastery_pct"] = round(entry["correct"] / entry["seen"], 3)

        # Update FSRS spaced-repetition scheduling.
        existing_fsrs = entry.get("fsrs")
        entry["fsrs"] = fsrs_scheduler.schedule_review(existing_fsrs, rating)

        # Rolling average answer latency (behavioral difficulty signal).
        if latency_secs is not None and latency_secs > 0:
            latency = entry.get("latency") or {"avg_secs": 0.0, "samples": 0}
            n = int(latency.get("samples", 0))
            avg = (float(latency.get("avg_secs", 0)) * n + latency_secs) / (n + 1)
            entry["latency"] = {"avg_secs": round(avg, 1), "samples": n + 1}

        mastery[concept] = entry
        await write_memory(session, "user", "", "concept_mastery", mastery)


async def get_concept_mastery(session: AsyncSession) -> dict[str, dict]:
    """Return the full concept_mastery dict (empty if none recorded)."""
    val = await read_memory(session, "user", "", "concept_mastery")
    if isinstance(val, dict):
        return val
    return {}


async def get_mastery_for_concepts(
    session: AsyncSession, concepts: list[str]
) -> list[dict]:
    """Return mastery info for a set of concepts, weakest first.

    Each entry: {concept, correct, wrong, seen, mastery_pct}. Concepts with no
    recorded history get a zero-seen placeholder so the agent knows they're new.
    """
    mastery = await get_concept_mastery(session)
    result: list[dict] = []
    for concept in concepts:
        concept = (concept or "").strip()
        if not concept:
            continue
        entry = mastery.get(concept)
        if entry:
            result.append({"concept": concept, **entry})
        else:
            # New concept the learner hasn't been tested on yet.
            result.append({
                "concept": concept,
                "correct": 0,
                "wrong": 0,
                "seen": 0,
                "mastery_pct": None,
            })
    # Sort: unseen first (None), then lowest mastery first.
    result.sort(
        key=lambda e: e["mastery_pct"] if e["mastery_pct"] is not None else -1
    )
    return result


async def get_due_concepts(
    session: AsyncSession, concepts: list[str] | None = None
) -> list[dict]:
    """Return concepts that are due for spaced-repetition review, most overdue first.

    A concept is "due" if its FSRS due date has passed, or if it has no FSRS
    state yet (new/untested = due immediately).

    Args:
        concepts: If provided, filter to only these concept names. If None,
                  returns all due concepts across all documents (for the
                  proactive agent).

    Returns a list of {concept, due_in_days, stability, mastery_pct, fsrs}.
    """
    from . import fsrs_scheduler

    mastery = await get_concept_mastery(session)
    result: list[dict] = []

    target_concepts = set(concepts) if concepts else set(mastery.keys())
    for concept in target_concepts:
        concept = (concept or "").strip()
        if not concept:
            continue
        entry = mastery.get(concept)
        fsrs = (entry or {}).get("fsrs")
        if not fsrs_scheduler.is_due(fsrs):
            continue
        result.append({
            "concept": concept,
            "due_in_days": fsrs_scheduler.due_in_days(fsrs),
            "stability": (fsrs or {}).get("stability"),
            "mastery_pct": (entry or {}).get("mastery_pct"),
            "fsrs": fsrs,
        })

    # Sort: most overdue first (most negative due_in_days), then new (None).
    result.sort(key=lambda e: e["due_in_days"] if e["due_in_days"] is not None else 999)
    return result


# --- Learner profile (who the learner is, for personalization) -------------
#
# learner_profile lives at scope="user", ref_id="", key="learner_profile" and
# grows automatically from every interaction. It captures the learner's level,
# preferred difficulty, preferred formats, study goal, and aggregate stats —
# the "who is this person" axis that complements concept_mastery's "what do
# they know" axis.
#
# All inference is deterministic (rules/heuristics, no LLM) so it stays fast
# and predictable. The profile starts as "unknown" defaults and fills in as
# the learner interacts.

PROFILE_KEY = "learner_profile"
MAX_SCORE_HISTORY = 20


def _default_profile() -> dict[str, Any]:
    return {
        "learner_level": "unknown",  # beginner | intermediate | advanced | unknown
        "preferred_difficulty": "medium",  # easy | medium | hard
        "preferred_formats": {
            "quiz_length": None,  # int (e.g. 8, 10, 12) or None
            "card_style": None,  # definition | application | mixed | None
            "notes_depth": None,  # concise | standard | detailed | None
        },
        "study_goal": "unknown",  # exam_prep | casual | skill_building | unknown
        "stats": {
            "total_quizzes": 0,
            "total_flashcard_reviews": 0,
            "avg_score": None,  # float 0..1 or None
            "score_history": [],  # list of {score, difficulty, ts}
            "flashcard_known_ratio": None,  # float 0..1 or None
            "first_interaction": None,  # iso timestamp
            "last_interaction": None,  # iso timestamp
        },
        "updated_at": None,
    }


def _derive_level(score_history: list[dict]) -> str:
    """Map rolling average score (cross-referenced with doc difficulty) to a
    learner level. Deterministic, no LLM.

    Thresholds (calibrated for a study context):
      - < 0.5 avg on easy/medium → beginner
      - 0.5–0.75 on medium → intermediate
      - > 0.75 on medium/hard → advanced
    """
    if not score_history:
        return "unknown"

    recent = score_history[-10:]  # last 10 quizzes
    scores = [s["score"] for s in recent if "score" in s]
    if not scores:
        return "unknown"

    avg = sum(scores) / len(scores)
    difficulties = [s.get("difficulty", "medium") for s in recent]
    # Weight: harder docs count more. Assign numeric weight per difficulty.
    diff_weight = {"easy": 0.5, "medium": 1.0, "hard": 1.5}
    total_weight = sum(diff_weight.get(d, 1.0) for d in difficulties)
    if total_weight == 0:
        total_weight = 1.0

    # Difficulty-adjusted score: if they're scoring well on hard docs, that
    # signals a higher level than the same score on easy docs.
    weighted_score = sum(
        s * diff_weight.get(d, 1.0)
        for s, d in zip(scores, difficulties)
    ) / total_weight

    if weighted_score >= 0.75:
        return "advanced"
    elif weighted_score >= 0.5:
        return "intermediate"
    else:
        return "beginner"


def _adjust_difficulty(current: str, recent_scores: list[float]) -> str:
    """Drift the preferred difficulty based on recent performance.

    Last 5 quizzes: avg > 0.8 → bump up; avg < 0.5 → ease off.
    """
    if len(recent_scores) < 3:
        return current  # not enough data to drift
    recent = recent_scores[-5:]
    avg = sum(recent) / len(recent)
    levels = ["easy", "medium", "hard"]
    idx = levels.index(current) if current in levels else 1
    if avg > 0.8 and idx < 2:
        return levels[idx + 1]
    if avg < 0.5 and idx > 0:
        return levels[idx - 1]
    return current


def _infer_goal_from_hint(hint: str) -> str | None:
    """Recognize study-goal keywords in a hint string."""
    h = hint.lower()
    if any(w in h for w in ["exam", "test", "final", "ap ", "sat", "gcse", "revision"]):
        return "exam_prep"
    if any(w in h for w in ["just learning", "casual", "curious", "fun"]):
        return "casual"
    if any(w in h for w in ["career", "job", "skill", "practice", "professional"]):
        return "skill_building"
    return None


def _infer_formats_from_hint(hint: str) -> dict[str, Any]:
    """Recognize format preferences in a hint string."""
    h = hint.lower()
    formats: dict[str, Any] = {}
    # Quiz length
    import re

    count_match = re.search(r"(\d+)\s*(?:question|q|quiz)", h)
    if count_match:
        n = int(count_match.group(1))
        if 3 <= n <= 20:
            formats["quiz_length"] = n
    # Card style
    if "definition" in h:
        formats["card_style"] = "definition"
    elif "application" in h or "scenario" in h or "example" in h:
        formats["card_style"] = "application"
    # Notes depth
    if "concise" in h or "brief" in h or "short" in h or "bullet" in h:
        formats["notes_depth"] = "concise"
    elif "detailed" in h or "in-depth" in h or "thorough" in h or "comprehensive" in h:
        formats["notes_depth"] = "detailed"
    return formats


async def get_learner_profile(session: AsyncSession) -> dict[str, Any]:
    """Return the learner profile, with defaults if none exists yet."""
    val = await read_memory(session, "user", "", PROFILE_KEY)
    if isinstance(val, dict):
        # Merge with defaults so new fields appear on old profiles.
        profile = _default_profile()
        profile.update(val)
        profile["preferred_formats"] = {
            **_default_profile()["preferred_formats"],
            **(val.get("preferred_formats") or {}),
        }
        profile["stats"] = {
            **_default_profile()["stats"],
            **(val.get("stats") or {}),
        }
        return profile
    return _default_profile()


async def update_learner_profile(
    session: AsyncSession,
    *,
    quiz_score: float | None = None,
    doc_difficulty: str | None = None,
    flashcard_results: list[dict] | None = None,
    hint: str | None = None,
) -> dict[str, Any]:
    """Update the learner profile from an interaction. Called after every quiz
    submit, flashcard review, and generate (for hint persistence).

    All arguments are optional — only the signals present in this interaction
    are used. Returns the updated profile.
    """
    async with blob_lock:
        profile = await get_learner_profile(session)
        stats = profile["stats"]
        now = datetime.now(timezone.utc).isoformat()

        if stats["first_interaction"] is None:
            stats["first_interaction"] = now
        stats["last_interaction"] = now

        # --- Quiz signal ---
        if quiz_score is not None:
            stats["total_quizzes"] = stats.get("total_quizzes", 0) + 1
            history = stats.get("score_history") or []
            history.append({
                "score": quiz_score,
                "difficulty": doc_difficulty or "medium",
                "ts": now,
            })
            stats["score_history"] = history[-MAX_SCORE_HISTORY:]
            # Rolling average
            all_scores = [h["score"] for h in stats["score_history"]]
            stats["avg_score"] = round(sum(all_scores) / len(all_scores), 3)

            # Recompute level from the full history
            profile["learner_level"] = _derive_level(stats["score_history"])

            # Drift preferred difficulty based on recent trend
            recent_scores = [h["score"] for h in stats["score_history"]]
            profile["preferred_difficulty"] = _adjust_difficulty(
                profile["preferred_difficulty"], recent_scores
            )

        # --- Flashcard signal ---
        if flashcard_results:
            stats["total_flashcard_reviews"] = (
                stats.get("total_flashcard_reviews", 0) + len(flashcard_results)
            )
            known = sum(1 for r in flashcard_results if r.get("known"))
            total = len(flashcard_results)
            if total > 0:
                # Rolling known ratio (simple average of current + new batch).
                prev = stats.get("flashcard_known_ratio")
                batch_ratio = known / total
                if prev is not None:
                    stats["flashcard_known_ratio"] = round((prev + batch_ratio) / 2, 3)
                else:
                    stats["flashcard_known_ratio"] = round(batch_ratio, 3)

        # --- Hint signal (explicit preferences from the user) ---
        if hint and hint.strip():
            hint = hint.strip()
            goal = _infer_goal_from_hint(hint)
            if goal:
                profile["study_goal"] = goal
            formats = _infer_formats_from_hint(hint)
            if formats:
                pf = profile["preferred_formats"]
                for k, v in formats.items():
                    pf[k] = v  # explicit hint overrides inferred

        # --- Infer study goal from cadence (if still unknown) ---
        if profile["study_goal"] == "unknown" and stats["total_quizzes"] >= 3:
            # High recent quiz frequency → likely exam prep
            history = stats.get("score_history") or []
            if len(history) >= 3:
                # Check if quizzes are clustered in time (within a few hours)
                from datetime import datetime as _dt

                timestamps = []
                for h in history[-5:]:
                    try:
                        timestamps.append(_dt.fromisoformat(h["ts"]))
                    except (KeyError, ValueError):
                        pass
                if len(timestamps) >= 3:
                    span = (max(timestamps) - min(timestamps)).total_seconds()
                    # 3+ quizzes within 6 hours suggests cramming / exam prep
                    if span < 6 * 3600:
                        profile["study_goal"] = "exam_prep"

        profile["stats"] = stats
        profile["updated_at"] = now
        await write_memory(session, "user", "", PROFILE_KEY, profile)
        return profile
