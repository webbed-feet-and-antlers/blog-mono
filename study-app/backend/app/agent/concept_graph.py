"""Concept knowledge graph — merging document analyses into mastery entries.

When a document is analyzed (see the DocumentIngested/DocumentAnalyzed
handlers in app/events/handlers/ingestion.py), the extracted concepts and
their relationships (prerequisites, related, part_of) are merged into the
global concept_mastery entries, creating a unified view: each concept knows
its mastery level AND its structural position in the knowledge graph.

The merge is additive — new concepts get entries, existing entries gain
prerequisites/related/documents/modules fields.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from . import memory as memory_store
from ..models import Document, Lesson, Module

logger = logging.getLogger(__name__)


async def merge_concept_graph(
    session: AsyncSession, doc_id: str, analysis: dict
) -> None:
    """Merge per-doc concept relationships into the global concept_mastery store.

    - Ensures every concept has a mastery entry (creates zero-mastery if new).
    - Adds doc_id to each concept's documents[] list.
    - Derives module/lesson name from the doc's hierarchy chain.
    - Adds prerequisite/related edges from concept_relationships.
    All additive and deduped.

    The whole read-modify-write runs under the memory blob lock so a
    concurrent quiz/flashcard review can't be clobbered by the merge (the
    store is one JSON row rewritten wholesale).
    """
    async with memory_store.blob_lock:
        mastery = await memory_store.get_concept_mastery(session)
        concepts = analysis.get("concepts") or []
        relationships = analysis.get("concept_relationships") or []

        # Derive the module/lesson name for this document (if filed in hierarchy).
        module_names = await _get_doc_module_names(session, doc_id)

        # Ensure every concept has an entry and record the doc + module context.
        for concept_name in concepts:
            concept_name = str(concept_name).strip()
            if not concept_name:
                continue
            entry = mastery.get(concept_name)
            if entry is None:
                # New concept — create a zero-mastery entry.
                entry = {"correct": 0, "wrong": 0, "seen": 0, "mastery_pct": None}
                mastery[concept_name] = entry

            # Add this doc to the concept's document list.
            docs = entry.setdefault("documents", [])
            if doc_id not in docs:
                docs.append(doc_id)

            # Add module context.
            if module_names:
                mods = entry.setdefault("modules", [])
                for mn in module_names:
                    if mn not in mods:
                        mods.append(mn)

        # Merge relationship edges.
        for rel in relationships:
            if not isinstance(rel, dict):
                continue
            source = str(rel.get("source", "")).strip()
            target = str(rel.get("target", "")).strip()
            rel_type = str(rel.get("type", "related")).strip()

            if not source or not target:
                continue

            # Ensure both concepts exist.
            for name in (source, target):
                if name not in mastery:
                    mastery[name] = {
                        "correct": 0,
                        "wrong": 0,
                        "seen": 0,
                        "mastery_pct": None,
                    }

            entry = mastery[source]
            if rel_type == "prerequisite":
                prereqs = entry.setdefault("prerequisites", [])
                if target not in prereqs:
                    prereqs.append(target)
            elif rel_type in ("related", "part_of"):
                related = entry.setdefault("related", [])
                if target not in related:
                    related.append(target)

        await memory_store.write_memory(
            session, "user", "", "concept_mastery", mastery
        )


async def _get_doc_module_names(session: AsyncSession, doc_id: str) -> list[str]:
    """Derive the module title(s) for a document.

    Resolves via the lesson hierarchy (doc → lesson → module) or, if the doc
    is filed directly under a module, via doc.module_id.
    """
    doc = await session.get(Document, doc_id)
    if doc is None:
        return []

    # Filed directly under a module (e.g. a textbook).
    if doc.module_id is not None:
        module = await session.get(Module, doc.module_id)
        if module is not None:
            return [module.title]

    # Filed under a lesson — resolve through the lesson's module.
    if doc.lesson_id is not None:
        lesson = await session.get(Lesson, doc.lesson_id)
        if lesson is None:
            return []
        module = await session.get(Module, lesson.module_id)
        if module is None:
            return []
        return [module.title]

    return []
