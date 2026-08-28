"""Proactive agent — a background job that learns from quiz misses and
pre-generates flashcard review decks for weak topics, without any user trigger.

This is the "proactive / async agent" product pattern: the user takes a quiz,
misses questions, and later opens the app to find a review deck already waiting.

The job reuses the existing LangGraph agent pipeline (run_generation) — it just
calls it with task_type="flashcards" and instructions targeting the learner's
weak areas. The weak-topics signal comes from the quiz feedback loop in
routes/quiz.py (which writes to agent memory). Tagged with origin="proactive"
so the frontend can surface it as "prepared for you".
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from .agent import memory as memory_store
from .agent.graph import run_generation
from .auth import user_scope, user_ref_id
from .config import settings
from .db import SessionLocal
from .models import AgentMemory, ContentItem

logger = logging.getLogger(__name__)


async def _known_user_ids() -> list[str]:
    """Distinct owners that have user-scope memory ("" = ambient user)."""
    async with SessionLocal() as session:
        rows = await session.execute(
            select(AgentMemory.user_id).where(
                AgentMemory.scope == "user",
                AgentMemory.user_id != "",
            ).distinct()
        )
        # The ambient user (legacy/tests) runs too so nothing silently stops.
        return [""] + [r[0] for r in rows.all()]


async def run_proactive_review() -> dict:
    """Run one proactive iteration per user, isolated per user.

    Finds each user's documents with weak topics (that don't already have a
    recent proactive deck), generates a review flashcard deck for each, and
    tags it as proactive.

    Returns a summary dict (useful for tests / the debug endpoint).
    """
    summary = {"checked": 0, "generated": 0, "skipped": 0, "errors": 0, "details": []}

    for uid in await _known_user_ids():
        with user_scope(uid):
            user_summary = await _run_proactive_for_current_user(summary)
    return summary


async def _run_proactive_for_current_user(summary: dict) -> dict:
    async with SessionLocal() as session:
        candidates = await memory_store.get_review_candidates(
            session, cooldown_hours=settings.proactive_cooldown_hours
        )
        summary["checked"] = len(candidates)

        for candidate in candidates:
            doc_id = candidate["document_id"]
            weak = candidate["weak_topics"]
            try:
                logger.info(
                    "[agent] proactive: generating review deck for doc %s (weak: %s)",
                    doc_id,
                    weak,
                )
                final_state = await run_generation(
                    document_id=doc_id,
                    document_text=candidate["document_text"],
                    task_type="flashcards",
                    session=session,
                    instructions=(
                        "Review deck targeting the learner's weak areas. Focus "
                        f"on active recall of these specific concepts: {', '.join(weak)}. "
                        "These are topics the learner has struggled with on past quizzes."
                    ),
                )

                if final_state.get("error"):
                    summary["errors"] += 1
                    summary["details"].append(
                        {"doc_id": doc_id, "status": "error", "error": final_state["error"]}
                    )
                    logger.warning("[agent] proactive: doc %s failed: %s", doc_id, final_state["error"])
                    continue

                content_item = final_state.get("content_item")
                if not content_item:
                    summary["errors"] += 1
                    continue

                # Tag the deck as proactive + give it a descriptive title.
                item = await session.get(ContentItem, content_item["id"])
                if item is not None:
                    item.content = {
                        **item.content,
                        "origin": "proactive",
                        "title": f"Review: {', '.join(weak[:3])}{'…' if len(weak) > 3 else ''}",
                    }

                await session.commit()
                summary["generated"] += 1
                summary["details"].append(
                    {
                        "doc_id": doc_id,
                        "content_id": content_item["id"],
                        "weak_topics": weak,
                        "status": "generated",
                    }
                )
                logger.info(
                    "[agent] proactive: generated deck %s for doc %s",
                    content_item["id"],
                    doc_id,
                )
            except Exception:
                summary["errors"] += 1
                summary["details"].append({"doc_id": doc_id, "status": "error"})
                logger.exception("[agent] proactive: doc %s raised", doc_id)

    logger.info(
        "[agent] proactive run complete for user %r: %d checked, %d generated, %d errors",
        user_ref_id() or "<ambient>",
        summary["checked"],
        summary["generated"],
        summary["errors"],
    )
    return summary


async def proactive_loop() -> None:
    """Background scheduler loop. Sleeps for the configured interval between runs.

    Designed to be spawned via asyncio.create_task in the FastAPI lifespan and
    cancelled on shutdown. Checks proactive_enabled each iteration so the flag
    can be toggled without restarting.
    """
    logger.info(
        "[agent] proactive loop started (interval=%ds, enabled=%s)",
        settings.proactive_interval_seconds,
        settings.proactive_enabled,
    )
    while True:
        await asyncio.sleep(settings.proactive_interval_seconds)
        if not settings.proactive_enabled:
            continue
        try:
            await run_proactive_review()
        except Exception:
            logger.exception("[agent] proactive loop: run failed")
        # Reflect on the learner's accumulated behavior (LLM narrative over
        # the activity ledger; cooldown-gated inside). Also runnable on
        # demand via POST /api/memory/reflect.
        try:
            from .agent.reflection import reflect_on_learner

            async with SessionLocal() as session:
                await reflect_on_learner(session)
                await session.commit()
        except Exception:
            logger.exception("[agent] reflection failed")
        # Periodically update recommendation strategy weights from telemetry
        # (the LinUCB bandit). Runs less frequently than proactive review.
        try:
            from .recommend.bandit import run_bandit_update

            await run_bandit_update()
        except Exception:
            logger.debug("[agent] bandit update skipped (may lack telemetry)")


async def get_proactive_decks(session) -> list[ContentItem]:
    """Return all proactive review decks (for the debug endpoint / frontend)."""
    result = await session.execute(
        select(ContentItem)
        .where(ContentItem.type == "flashcards")
        .order_by(ContentItem.created_at.desc())
    )
    return [
        item
        for item in result.scalars().all()
        if isinstance(item.content, dict) and item.content.get("origin") == "proactive"
    ]
