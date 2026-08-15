"""Study plan generation — the agent plans a module, paced toward an exam.

Grounding (deterministic): the module's lessons + documents (with quiz/deck
availability and read state), the module's concepts resolved by doc-id
intersection with the mastery store (id-based — the title-based `modules[]`
field goes stale on renames), FSRS due/retrievability summary, weak topics,
the learner profile + behavioral insights, and days-to-exam.

One LLM call turns that into ordered, day-bucketed items of constrained
types. Validation then maps the LLM's document references back to real ids
(dropping hallucinations) and carries over completion from the previous
version — regenerating mid-semester must never lose progress.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..llm import chat_json
from ..models import ContentItem, Document, Lesson, Module, StudyPlan
from . import behavior as behavior_store
from . import fsrs_scheduler
from . import memory as memory_store

logger = logging.getLogger(__name__)


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes — coerce to aware UTC for arithmetic."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


ITEM_TYPES = (
    "review_concepts",
    "take_quiz",
    "generate_quiz",
    "review_deck",
    "generate_flashcards",
    "read_document",
)

MAX_DAYS_HORIZON = 14  # cap the plan horizon even when the exam is far

_SYSTEM_PROMPT = (
    "You are a study planner for a university module. Given the module's "
    "documents, the learner's per-concept mastery and spaced-repetition "
    "schedule, weak topics, and (when set) an exam date, produce a study "
    "plan as a JSON list of items.\n\n"
    "RULES:\n"
    "- Write entirely in English.\n"
    "- Only reference document titles and concept names that appear in the "
    "input. Never invent documents or concepts.\n"
    "- item types must be one of: " + ", ".join(ITEM_TYPES) + ".\n"
    "- day_offset: 0 = today, 1 = tomorrow, … Keep the plan within the "
    "requested horizon. Aim for ≤45 minutes of work per day.\n"
    "- Sequence along prerequisite chains (foundations first). Interleave "
    "spaced review of due/weak concepts with new material.\n"
    "- If the exam date is close, weight heavily toward due review and weak "
    "concepts; if far, balance new coverage with light maintenance review.\n"
    "- Schedule generate_quiz/generate_flashcards for documents that have "
    "no material yet; take_quiz/review_deck for those that do.\n"
    "- Each rationale must cite the data behind it (due count, recall %, "
    "missing material, exam proximity).\n\n"
    'Return ONLY JSON: {"items": [{"type": str, "title": str, "rationale": '
    "str, \"day_offset\": int, \"estimate_mins\": int, \"document_title\": "
    "str | null, \"concepts\": [str] | null}]}"
)


async def get_module_doc_ids(session: AsyncSession, module_id: str) -> set[str]:
    """All document ids belonging to a module — filed directly or via a
    lesson. Used by the planner and the module-scoped study session."""
    doc_ids: set[str] = set()
    direct = await session.execute(
        select(Document.id).where(Document.module_id == module_id)
    )
    doc_ids.update(row[0] for row in direct.all())
    lessons = await session.execute(
        select(Lesson.id).where(Lesson.module_id == module_id)
    )
    for (lesson_id,) in lessons.all():
        via_lesson = await session.execute(
            select(Document.id).where(Document.lesson_id == lesson_id)
        )
        doc_ids.update(row[0] for row in via_lesson.all())
    return doc_ids


async def generate_study_plan(
    session: AsyncSession, module_id: str
) -> StudyPlan | None:
    """Generate (or regenerate) the module's study plan. Returns the saved
    StudyPlan, or None if the module doesn't exist / has no content to plan
    over. Raises on LLM failure — callers decide how to surface that."""
    module = await session.get(Module, module_id)
    if module is None:
        return None

    doc_ids = await get_module_doc_ids(session, module_id)
    if not doc_ids:
        return None

    grounding = await _build_grounding(session, module, doc_ids)

    result = await chat_json(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Module: {module.title}\nPlan this module's study.\n\n"
                    + _render(grounding)
                ),
            },
        ],
        temperature=0.2,
        max_tokens=2500,
    )

    raw_items = result.get("items") or []
    items = _validate_items(raw_items, grounding["documents"])

    # Carry over completion from the previous version (match type + target).
    existing = await _get_plan(session, module_id)
    if existing is not None:
        _carry_over_progress(existing.items, items)
        existing.version = existing.version + 1
        plan = existing
    else:
        plan = StudyPlan(id=uuid.uuid4().hex[:12], module_id=module_id, version=1)
        session.add(plan)

    plan.items = items
    plan.stale_reasons = []
    plan.generated_at = datetime.now(timezone.utc)
    plan.meta = {
        "days_to_exam": grounding.get("days_to_exam"),
        "horizon_days": grounding.get("horizon_days"),
        "doc_count": len(doc_ids),
        "concept_count": grounding.get("concept_count", 0),
        "due_now": grounding.get("fsrs", {}).get("due_now", 0),
    }
    await session.commit()
    await session.refresh(plan)
    logger.info(
        "[planner] plan for module %s v%s: %d items (%d carried done)",
        module_id,
        plan.version,
        len(items),
        sum(1 for i in items if i.get("status") == "done"),
    )
    return plan


async def get_plan_with_staleness(
    session: AsyncSession, module_id: str
) -> tuple[StudyPlan, dict] | None:
    """Latest plan plus time-based staleness reasons computed on read."""
    plan = await _get_plan(session, module_id)
    if plan is None:
        return None
    module = await session.get(Module, module_id)
    reasons = list(plan.stale_reasons or [])
    age_days = (datetime.now(timezone.utc) - _as_utc(plan.generated_at)).total_seconds() / 86400
    if module is not None and module.exam_date is not None:
        days_to_exam = (module.exam_date - date.today()).days
        if days_to_exam <= 7 and age_days >= 2:
            reasons.append("exam is close and the plan is more than 2 days old")
    staleness = {"stale": bool(reasons), "reasons": reasons}
    return plan, staleness


async def _get_plan(session: AsyncSession, module_id: str) -> StudyPlan | None:
    result = await session.execute(
        select(StudyPlan).where(StudyPlan.module_id == module_id)
    )
    return result.scalars().first()


async def _build_grounding(
    session: AsyncSession, module: Module, doc_ids: set[str]
) -> dict[str, Any]:
    """Everything the planner prompt is allowed to know about the module."""
    # Documents with topics + material availability + read state.
    docs = []
    for doc_id in doc_ids:
        doc = await session.get(Document, doc_id)
        if doc is None:
            continue
        content = await session.execute(
            select(ContentItem.type).where(ContentItem.document_id == doc_id)
        )
        types = {row[0] for row in content.all()}
        docs.append({
            "id": doc_id,
            "title": doc.filename,
            "kind": doc.kind,
            "topic": None,  # filled below from analyses
            "has_quiz": "quiz" in types,
            "has_deck": "flashcards" in types,
        })
    topics = await memory_store.get_doc_topics(session)
    for d in docs:
        d["topic"] = topics.get(d["id"])

    # Module concepts: mastery entries referencing any module doc (id-based).
    mastery = await memory_store.get_concept_mastery(session)
    module_concepts: list[dict] = []
    now = datetime.now(timezone.utc)
    for name, entry in mastery.items():
        if not (set(entry.get("documents") or []) & doc_ids):
            continue
        fsrs = entry.get("fsrs")
        module_concepts.append({
            "concept": name,
            "mastery_pct": entry.get("mastery_pct"),
            "seen": entry.get("seen", 0),
            "due": bool(fsrs and fsrs_scheduler.is_due(fsrs, now)),
            "retrievability": round(
                fsrs_scheduler.retrievability(fsrs, now) or 0, 2
            ) if fsrs else None,
            "prerequisites": entry.get("prerequisites") or [],
        })
    module_concepts.sort(key=lambda c: (c["retrievability"] is not None, c["retrievability"] if c["retrievability"] is not None else 2))
    due_now = [c for c in module_concepts if c["due"]]

    weak = await memory_store.get_weak_topics(session)
    profile = await memory_store.get_learner_profile(session)
    insights = await memory_store.read_memory(session, "user", "", "learner_insights")
    engagement = await behavior_store.get_engagement(session)
    read_docs = set((engagement.get("docs") or {}).keys())

    days_to_exam = None
    horizon = 7
    if module.exam_date is not None:
        days_to_exam = (module.exam_date - date.today()).days
        horizon = max(3, min(days_to_exam, MAX_DAYS_HORIZON))

    return {
        "exam_date": module.exam_date.isoformat() if module.exam_date else None,
        "days_to_exam": days_to_exam,
        "horizon_days": horizon,
        "documents": docs,
        "unread_document_ids": sorted(doc_ids - read_docs),
        "concept_count": len(module_concepts),
        "weakest_concepts": module_concepts[:10],
        "fsrs": {
            "due_now": len(due_now),
            "due_names": [c["concept"] for c in due_now][:12],
            "avg_retrievability": (
                round(
                    sum(c["retrievability"] for c in module_concepts if c["retrievability"] is not None)
                    / max(1, sum(1 for c in module_concepts if c["retrievability"] is not None)),
                    2,
                )
                if any(c["retrievability"] is not None for c in module_concepts)
                else None
            ),
        },
        "weak_topics": [w.get("topic") for w in weak[:8]],
        "learner": {
            "level": profile.get("learner_level"),
            "preferred_difficulty": profile.get("preferred_difficulty"),
            "avg_score": (profile.get("stats") or {}).get("avg_score"),
            "insights_summary": (insights or {}).get("summary")
            if isinstance(insights, dict)
            else None,
        },
    }


def _validate_items(
    raw_items: list, docs: list[dict]
) -> list[dict]:
    """Map LLM output to deep-linkable items; drop anything hallucinated."""
    by_title: dict[str, dict] = {}
    for d in docs:
        by_title[d["title"].lower()] = d
        if d.get("topic"):
            by_title.setdefault(d["topic"].lower(), d)

    items: list[dict] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        itype = str(raw.get("type", "")).strip()
        if itype not in ITEM_TYPES:
            continue
        title = str(raw.get("title", "")).strip()[:120]
        if not title:
            continue

        # Resolve the document reference (title match, case-insensitive).
        doc_ref = str(raw.get("document_title") or "").strip()
        target_doc = by_title.get(doc_ref.lower()) if doc_ref else None
        if doc_ref and target_doc is None and itype in (
            "take_quiz",
            "generate_quiz",
            "review_deck",
            "generate_flashcards",
            "read_document",
        ):
            continue  # hallucinated document — drop the item

        concepts = [
            str(c)[:80]
            for c in (raw.get("concepts") or [])[:12]
            if str(c).strip()
        ]

        items.append({
            "id": uuid.uuid4().hex[:10],
            "type": itype,
            "title": title,
            "rationale": str(raw.get("rationale", "")).strip()[:220],
            "day_offset": max(0, min(int(raw.get("day_offset") or 0), MAX_DAYS_HORIZON - 1)),
            "estimate_mins": max(5, min(int(raw.get("estimate_mins") or 15), 90)),
            "status": "pending",
            "done_at": None,
            "done_reason": None,
            "done_kind": None,
            "target": {
                "document_id": target_doc["id"] if target_doc else None,
                "concepts": concepts or None,
            },
        })

    # Stable ordering for the UI.
    items.sort(key=lambda i: (i["day_offset"], i["type"]))
    if not items:
        # Fallback so a plan is never empty: review what's due.
        items.append({
            "id": uuid.uuid4().hex[:10],
            "type": "review_concepts",
            "title": "Review due concepts for this module",
            "rationale": "Default item — the generated plan was empty.",
            "day_offset": 0,
            "estimate_mins": 15,
            "status": "pending",
            "done_at": None,
            "done_reason": None,
            "done_kind": None,
            "target": {"document_id": None, "concepts": None},
        })
    return items


def _carry_over_progress(old_items: list[dict], new_items: list[dict]) -> None:
    """Preserve completion across regeneration: match by type + document
    (or type + overlapping concept set for review items)."""

    def key(i: dict) -> tuple:
        t = i.get("target") or {}
        concepts = tuple(sorted(t.get("concepts") or []))
        return (i.get("type"), t.get("document_id"), concepts)

    done = {key(i): i for i in (old_items or []) if i.get("status") == "done"}
    for item in new_items:
        match = done.get(key(item))
        if match is not None:
            item["status"] = "done"
            item["done_at"] = match.get("done_at")
            item["done_reason"] = match.get("done_reason")
            item["done_kind"] = match.get("done_kind")


def _render(grounding: dict[str, Any]) -> str:
    import json

    return json.dumps(grounding, default=str, indent=1)
