"""Memory routes — transparency into the agent's understanding of the learner.

The profile endpoint powers the understanding panel: deterministic profile +
study patterns + engagement + the LLM reflection's learner_insights, all in
one response. The reflect endpoint forces a fresh reflection (cooldown
bypass) — the panel's "Refresh understanding" button.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent import behavior as behavior_store
from ..agent.memory import get_learner_profile, list_memory
from ..db import get_session
from ..models import ContentItem
from ..schemas import AgentMemoryOut, ContentItemOut

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("", response_model=list[AgentMemoryOut])
async def get_memory(
    scope: str | None = Query(default=None),
    ref_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    return await list_memory(session, scope=scope, ref_id=ref_id)


@router.get("/proactive", response_model=list[ContentItemOut], tags=["proactive"])
async def get_proactive_decks(session: AsyncSession = Depends(get_session)):
    """List all proactive review decks the agent has generated on its own."""
    from sqlalchemy import select

    result = await session.execute(
        select(ContentItem)
        .where(ContentItem.type == "flashcards")
        .order_by(ContentItem.created_at.desc())
    )
    return [
        item
        for item in result.scalars().all()
        if isinstance(item.content, dict)
        and item.content.get("origin") == "proactive"
    ]


@router.get("/profile", tags=["profile"])
async def get_profile(session: AsyncSession = Depends(get_session)):
    """The agent's full understanding of the learner, in one response:

    profile (deterministic heuristics) + insights (LLM reflection) +
    patterns (when/how they study) + engagement (what they do) +
    slow-recall concepts (behavioral difficulty signals).
    """
    from ..agent.memory import get_doc_topics
    from ..agent.reflection import get_learner_insights

    profile = await get_learner_profile(session)
    patterns = await behavior_store.get_study_patterns(session)
    engagement = await behavior_store.get_engagement(session)
    insights = await get_learner_insights(session)
    slow = await behavior_store.get_slow_concepts(session)

    # Resolve doc topics for the top-dwell documents (nicer than raw ids).
    docs = engagement.get("docs") or {}
    topics = await get_doc_topics(session)
    top_docs = sorted(
        docs.items(), key=lambda kv: kv[1].get("dwell_secs", 0), reverse=True
    )[:5]
    top_doc_views = [
        {
            "doc_id": doc_id,
            "topic": topics.get(doc_id),
            "views": v.get("views", 0),
            "dwell_secs": round(v.get("dwell_secs", 0)),
        }
        for doc_id, v in top_docs
        if v.get("dwell_secs", 0) > 0
    ]

    return {
        **profile,
        "insights": insights,
        "patterns": patterns,
        "engagement": {
            "total_dwell_secs": round(engagement.get("total_dwell_secs", 0)),
            "actions_count": engagement.get("actions_count", 0),
            "tab_switches": engagement.get("tab_switches"),
            "top_docs": top_doc_views,
        },
        "slow_concepts": slow,
    }


@router.post("/reflect", tags=["profile"])
async def reflect(
    force: bool = Query(default=True),
    session: AsyncSession = Depends(get_session),
):
    """Run the LLM reflection over the behavior ledger now.

    Default force=true (the panel button): bypasses the cooldown so the
    user can see their updated understanding immediately. Pass force=false
    to respect the cooldown.
    """
    from ..agent.reflection import reflect_on_learner

    result = await reflect_on_learner(session, force=force)
    if result.get("status") == "updated":
        await session.commit()
    return result
