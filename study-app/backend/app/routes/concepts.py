"""Concept listing endpoint — surfaces the knowledge graph + mastery model.

GET /api/concepts returns all concepts with their mastery level, FSRS due
status, prerequisites (and the mastery of each prerequisite), related
concepts, and module context. This is the unified view that powers the
Concepts tab and lets the user see what the agent knows about their learning.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent import memory as memory_store
from ..agent import fsrs_scheduler
from ..db import get_session

router = APIRouter(prefix="/api/concepts", tags=["concepts"])


@router.get("")
async def list_concepts(session: AsyncSession = Depends(get_session)):
    """Return all concepts with mastery, FSRS status, and graph relationships."""
    mastery = await memory_store.get_concept_mastery(session)

    result: list[dict[str, Any]] = []
    for concept, data in mastery.items():
        fsrs = data.get("fsrs")
        prereqs = data.get("prerequisites") or []
        related = data.get("related") or []

        # Compute the mastery of each prerequisite (join against concept_mastery).
        prereq_mastery = []
        for prereq in prereqs:
            pdata = mastery.get(prereq, {})
            prereq_mastery.append({
                "concept": prereq,
                "mastery_pct": pdata.get("mastery_pct"),
                "seen": pdata.get("seen", 0),
            })

        # A concept is "blocked" if any prerequisite has low mastery.
        prereq_blocked = any(
            pm["mastery_pct"] is not None and pm["mastery_pct"] < 0.5
            for pm in prereq_mastery
        )

        result.append({
            "concept": concept,
            "mastery_pct": data.get("mastery_pct"),
            "seen": data.get("seen", 0),
            "correct": data.get("correct", 0),
            "wrong": data.get("wrong", 0),
            "due": fsrs_scheduler.is_due(fsrs),
            "due_in_days": fsrs_scheduler.due_in_days(fsrs),
            "prerequisites": prereqs,
            "related": related,
            "documents": data.get("documents") or [],
            "modules": data.get("modules") or [],
            "prerequisite_mastery": prereq_mastery,
            "prerequisite_blocked": prereq_blocked,
        })

    # Sort: due first, then weakest, then prerequisite-blocked, then alphabetical.
    def sort_key(e: dict) -> tuple:
        due_rank = 0 if e["due"] else 1
        mastery = e["mastery_pct"]
        if mastery is None:
            mastery_rank = 0  # untested = highest priority
        else:
            mastery_rank = mastery
        blocked_rank = 0 if e["prerequisite_blocked"] else 1
        return (due_rank, mastery_rank, blocked_rank, e["concept"].lower())

    result.sort(key=sort_key)
    return result
