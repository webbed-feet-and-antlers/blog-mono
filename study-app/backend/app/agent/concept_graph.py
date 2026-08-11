"""Concept knowledge graph — background analysis and graph merging.

When a document is uploaded, a background task silently analyzes it and
extracts not just concepts but their relationships (prerequisites, related,
part_of). These relationships are merged into the global concept_mastery
entries, creating a unified view: each concept knows its mastery level AND
its structural position in the knowledge graph.

The merge is additive — new concepts get entries, existing entries gain
prerequisites/related/documents/modules fields. This runs silently in the
background so the user experiences the "ambient intelligence" pattern: upload
a doc, walk away, come back to a concept graph already built.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from . import memory as memory_store
from . import tools
from ..db import SessionLocal
from ..models import Document, Lesson, Module

logger = logging.getLogger(__name__)


async def analyze_concepts_background(doc_id: str) -> None:
    """Background task: analyze a document's concepts + relationships and merge
    them into the global concept_mastery store.

    Called via asyncio.create_task from the upload route. Creates its own
    session (the request session is closed by then). Fails silently — a
    background task must not crash the server.
    """
    try:
        async with SessionLocal() as session:
            doc = await session.get(Document, doc_id)
            if doc is None:
                logger.warning("[concept-graph] doc %s not found", doc_id)
                return

            logger.info("[concept-graph] analyzing doc %s (%s)", doc_id, doc.filename)
            analysis = await tools.analyze_document(doc.text)

            # Cache the analysis so the first Generate call is instant.
            await memory_store.write_memory(
                session, "doc", doc_id, "analysis", analysis
            )

            # Merge concept relationships into the global graph.
            await merge_concept_graph(session, doc_id, analysis)

            await session.commit()
            concepts = analysis.get("concepts") or []
            rels = analysis.get("concept_relationships") or []
            logger.info(
                "[concept-graph] doc %s: %d concepts, %d relationships merged",
                doc_id,
                len(concepts),
                len(rels),
            )
    except Exception:
        logger.exception("[concept-graph] background analysis failed for doc %s", doc_id)


async def merge_concept_graph(
    session, doc_id: str, analysis: dict
) -> None:
    """Merge per-doc concept relationships into the global concept_mastery store.

    - Ensures every concept has a mastery entry (creates zero-mastery if new).
    - Adds doc_id to each concept's documents[] list.
    - Derives module/lesson name from the doc's hierarchy chain.
    - Adds prerequisite/related edges from concept_relationships.
    All additive and deduped.
    """
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

    await memory_store.write_memory(session, "user", "", "concept_mastery", mastery)


async def _get_doc_module_names(session, doc_id: str) -> list[str]:
    """Derive the module title(s) for a document via its lesson hierarchy."""
    doc = await session.get(Document, doc_id)
    if doc is None or doc.lesson_id is None:
        return []

    lesson = await session.get(Lesson, doc.lesson_id)
    if lesson is None:
        return []

    module = await session.get(Module, lesson.module_id)
    if module is None:
        return []

    return [module.title]
