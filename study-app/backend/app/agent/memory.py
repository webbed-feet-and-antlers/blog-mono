"""Agent memory read/write helpers backed by the agent_memory table.

Scope:
  - "doc":  ref_id = document_id. Caches analysis, extracted concepts, prior generations.
  - "user": ref_id = "" (single local user for the POC). Cross-document learnings.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AgentMemory


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
    if not weak:
        return []
    weak_names = {w["topic"] for w in weak}

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
        # Intersect the doc's concepts with the learner's weak topics.
        relevant_weak = sorted(weak_names & doc_concepts)
        if not relevant_weak:
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
            "weak_topics": relevant_weak,
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
    session: AsyncSession, concept: str, correct: bool
) -> None:
    """Record one interaction outcome for a concept.

    Increments seen + (correct|wrong), recomputes mastery_pct. Concept mastery
    is cross-document (user scope) so mastering a concept in one doc carries
    over. Skips empty/whitespace concept strings.
    """
    concept = (concept or "").strip()
    if not concept:
        return

    mastery = await get_concept_mastery(session)
    entry = mastery.get(concept) or {"correct": 0, "wrong": 0, "seen": 0}
    entry["seen"] = entry["seen"] + 1
    if correct:
        entry["correct"] = entry["correct"] + 1
    else:
        entry["wrong"] = entry["wrong"] + 1
    entry["mastery_pct"] = round(entry["correct"] / entry["seen"], 3)
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
