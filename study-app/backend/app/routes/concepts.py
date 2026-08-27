"""Concept listing endpoint — surfaces the knowledge graph + mastery model.

GET /api/concepts returns all concepts with their mastery level, FSRS due
status, prerequisites (and the mastery of each prerequisite), related
concepts, and module context. This is the unified view that powers the
Concepts tab and lets the user see what the agent knows about their learning.

GET /api/concepts/{name}/references returns everywhere a concept appears:
the documents it was extracted from, the quiz questions tagged with it, and
the flashcards that test it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..agent import memory as memory_store
from ..agent import fsrs_scheduler
from ..db import get_session
from ..models import ContentItem, Document

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
            # Continuous recall probability right now (FSRS power law).
            # None for untested concepts. Better "how well do I know this today"
            # signal than cumulative accuracy for spaced repetition.
            "retrievability": fsrs_scheduler.retrievability(fsrs),
            "stability": (fsrs or {}).get("stability"),
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


@router.get("/{concept_name}/references")
async def get_concept_references(
    concept_name: str, session: AsyncSession = Depends(get_session)
):
    """Return everything that references a concept.

    - documents: the docs the concept was extracted from (from the mastery
      store's documents list), resolved to filenames + topics
    - quiz_questions: questions tagged with this concept (each question
      carries a `concept` from generation time)
    - flashcards: cards tagged with this concept
    """
    mastery = await memory_store.get_concept_mastery(session)

    # Case-insensitive exact-name lookup against the mastery store.
    target = concept_name.strip().lower()
    entry = next(
        (
            (name, data)
            for name, data in mastery.items()
            if name.strip().lower() == target
        ),
        None,
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="Concept not found")
    concept, data = entry
    fsrs = data.get("fsrs")

    # Resolve document topics (same helper as the module-tree endpoint).
    topics = await memory_store.get_doc_topics(session)

    # Documents the concept appears in.
    documents = []
    for doc_id in data.get("documents") or []:
        d = await session.get(Document, doc_id)
        if d is not None:
            documents.append(
                {"id": d.id, "filename": d.filename, "topic": topics.get(d.id)}
            )

    # Scan quizzes + flashcard decks for items tagged with this concept.
    content_result = await session.execute(
        select(ContentItem)
        .options(selectinload(ContentItem.document))
        .where(ContentItem.type.in_(["quiz", "flashcards"]))
    )
    items = content_result.scalars().all()

    quiz_questions: list[dict[str, Any]] = []
    flashcards: list[dict[str, Any]] = []
    for item in items:
        doc_name = item.document.filename if item.document else None
        if item.type == "quiz":
            for q in item.content.get("questions", []):
                if (q.get("concept") or "").strip().lower() != target:
                    continue
                quiz_questions.append(
                    {
                        "content_id": item.id,
                        "document_id": item.document_id,
                        "doc_filename": doc_name,
                        "question_id": q.get("id"),
                        "prompt": q.get("prompt", ""),
                    }
                )
        elif item.type == "flashcards":
            for c in item.content.get("cards", []):
                if (c.get("concept") or "").strip().lower() != target:
                    continue
                variants = c.get("variants") or []
                flashcards.append(
                    {
                        "content_id": item.id,
                        "document_id": item.document_id,
                        "doc_filename": doc_name,
                        "card_id": c.get("id"),
                        "front": (
                            variants[0].get("front")
                            if variants
                            else c.get("front", "")
                        ),
                        "back": (
                            variants[0].get("back")
                            if variants
                            else c.get("back", "")
                        ),
                    }
                )

    return {
        "concept": concept,
        "mastery_pct": data.get("mastery_pct"),
        "seen": data.get("seen", 0),
        "correct": data.get("correct", 0),
        "wrong": data.get("wrong", 0),
        "retrievability": fsrs_scheduler.retrievability(fsrs),
        "due": fsrs_scheduler.is_due(fsrs),
        "modules": data.get("modules") or [],
        "documents": documents,
        "quiz_questions": quiz_questions,
        "flashcards": flashcards,
    }
