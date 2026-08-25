"""Study plan reactions — adaptation over the semester.

Three behaviors, all on the bus:

  Staleness detection — new content analyzed in a module, or quiz results
  on a module document, mark the plan stale and publish
  StudyPlanStaleDetected.

  Throttled regeneration — the background handler regenerates a stale plan
  at most once per module per day (LLM cost control). Progress carries over
  (planner matches type+target across versions).

  Progress auto-detection — quizzes taken, decks generated, and study
  sessions completed mark matching plan items done with a reason, so the
  plan notices when the student follows it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...agent import planner as planner_store
from ...models import Document, Lesson, StudyPlan
from .. import bus
from ..domain import (
    DocumentAnalyzed,
    GenerationCompleted,
    QuizAttempted,
    StudyPlanStaleDetected,
    StudySessionReviewed,
)

logger = logging.getLogger(__name__)

# Auto-regeneration cooldown per module.
REGEN_COOLDOWN_SECS = 24 * 3600


def _touch_items(plan: StudyPlan) -> None:
    """Mark the JSON items column dirty — SQLAlchemy doesn't track in-place
    mutations of JSON columns, so without this the commit would no-op."""
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(plan, "items")


# --- Staleness detection -----------------------------------------------------


@bus.on(DocumentAnalyzed)
async def mark_stale_on_new_content(
    event: DocumentAnalyzed, session: AsyncSession
) -> None:
    """A document in a planned module was (re)analyzed — new content the
    plan doesn't know about."""
    module_id = await _doc_module_id(session, event.document_id)
    if module_id is None:
        return
    plan = await _get_plan(session, module_id)
    if plan is None:
        return
    reason = "new content analyzed"
    if reason not in (plan.stale_reasons or []):
        plan.stale_reasons = [*(plan.stale_reasons or []), reason]
    await session.commit()
    await bus.publish(StudyPlanStaleDetected(module_id=module_id, reason=reason))


@bus.on(QuizAttempted)
async def mark_progress_from_quiz(event: QuizAttempted, session: AsyncSession) -> None:
    """A quiz attempt both (a) marks matching take_quiz items done and
    (b) signals staleness — mastery shifted, the plan's rationales age."""
    if not event.document_id:
        return
    module_id = await _doc_module_id(session, event.document_id)
    if module_id is None:
        return
    plan = await _get_plan(session, module_id)
    if plan is None:
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    changed = False
    for item in plan.items or []:
        target = item.get("target") or {}
        if (
            item.get("type") in ("take_quiz", "review_deck")
            and item.get("status") != "done"
            and target.get("document_id") == event.document_id
        ):
            item["status"] = "done"
            item["done_at"] = now_iso
            item["done_kind"] = "auto"
            item["done_reason"] = f"Quiz taken — {event.correct}/{event.total}"
            changed = True
    if changed:
        _touch_items(plan)

    reason = "quiz results"
    if reason not in (plan.stale_reasons or []):
        plan.stale_reasons = [*(plan.stale_reasons or []), reason]

    await session.commit()
    await bus.publish(StudyPlanStaleDetected(module_id=module_id, reason=reason))


@bus.on(GenerationCompleted)
async def mark_progress_from_generation(
    event: GenerationCompleted, session: AsyncSession
) -> None:
    """Generated material satisfies generate_* plan items for that document."""
    module_id = await _doc_module_id(session, event.document_id)
    if module_id is None:
        return
    plan = await _get_plan(session, module_id)
    if plan is None:
        return

    wanted = {
        "quiz": "generate_quiz",
        "flashcards": "generate_flashcards",
    }.get(event.task_type)
    if wanted is None:
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    for item in plan.items or []:
        target = item.get("target") or {}
        if (
            item.get("type") == wanted
            and item.get("status") != "done"
            and target.get("document_id") == event.document_id
        ):
            item["status"] = "done"
            item["done_at"] = now_iso
            item["done_kind"] = "auto"
            item["done_reason"] = f"Agent generated {event.task_type}"
            _touch_items(plan)
            await session.commit()
            return


@bus.on(StudySessionReviewed)
async def mark_progress_from_session(
    event: StudySessionReviewed, session: AsyncSession
) -> None:
    """A completed study session covering ≥50% of a review item's concepts
    marks that item done."""
    reviewed = {r.concept.strip().lower() for r in event.results if r.concept}
    if not reviewed:
        return

    plans = await session.execute(select(StudyPlan))
    now_iso = datetime.now(timezone.utc).isoformat()
    for plan in plans.scalars().all():
        changed = False
        for item in plan.items or []:
            if item.get("type") != "review_concepts" or item.get("status") == "done":
                continue
            concepts = item.get("target", {}).get("concepts") or []
            if not concepts:
                continue
            covered = sum(1 for c in concepts if str(c).strip().lower() in reviewed)
            if covered / len(concepts) >= 0.5:
                item["status"] = "done"
                item["done_at"] = now_iso
                item["done_kind"] = "auto"
                item["done_reason"] = f"Reviewed {covered}/{len(concepts)} concepts"
                changed = True
        if changed:
            _touch_items(plan)
            await session.commit()


# --- Throttled regeneration ----------------------------------------------------


@bus.on(StudyPlanStaleDetected, background=True)
async def regenerate_stale_plan(
    event: StudyPlanStaleDetected, session: AsyncSession
) -> None:
    """Regenerate the module's plan when it's stale and the daily cooldown
    allows. One LLM call; observable in /api/events like every agent action."""
    plan = await _get_plan(session, event.module_id)
    if plan is None:
        return
    if not (plan.stale_reasons or []):
        return

    from ...agent.planner import _as_utc

    age = (datetime.now(timezone.utc) - _as_utc(plan.generated_at)).total_seconds()
    if age < REGEN_COOLDOWN_SECS:
        logger.info(
            "[plans] module %s stale (%s) but regenerated %.1fh ago — waiting",
            event.module_id,
            event.reason,
            age / 3600,
        )
        return

    logger.info(
        "[plans] regenerating plan for module %s (stale: %s)",
        event.module_id,
        event.reason,
    )
    await planner_store.generate_study_plan(session, event.module_id)


# --- Helpers -------------------------------------------------------------------


async def _doc_module_id(session: AsyncSession, doc_id: str) -> str | None:
    """Resolve a document to its module — direct filing or via its lesson."""
    doc = await session.get(Document, doc_id)
    if doc is None:
        return None
    if doc.module_id is not None:
        return doc.module_id
    if doc.lesson_id is not None:
        lesson = await session.get(Lesson, doc.lesson_id)
        if lesson is not None:
            return lesson.module_id
    return None


async def _get_plan(session: AsyncSession, module_id: str) -> StudyPlan | None:
    result = await session.execute(
        select(StudyPlan).where(StudyPlan.module_id == module_id)
    )
    return result.scalars().first()
