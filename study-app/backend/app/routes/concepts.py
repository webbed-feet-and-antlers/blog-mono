"""Concept endpoints — the knowledge graph + mastery model.

GET /api/concepts returns all concepts with their mastery level, FSRS due
status, prerequisites (and the mastery of each prerequisite), related
concepts, and module context. This is the unified view that powers the
Concepts tab and lets the user see what the agent knows about their learning.

GET /api/concepts/{name}/references returns everywhere a concept appears:
the documents it was extracted from, the quiz questions tagged with it, and
the flashcards that test it.

GET /api/concepts/graph is the structural view: nodes and typed edges from
the concepts/concept_edges tables (recursive traversal when ?root=&depth=
are given), merged with mastery-store-only concepts so the graph is useful
from day one, before anything populates the tables. POST endpoints create
concepts and edges (LLM extraction is a follow-up).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..agent import memory as memory_store
from ..agent import fsrs_scheduler
from ..auth import get_current_user, user_ref_id
from ..db import get_session
from ..models import Concept, ConceptEdge, ContentItem, Document, Module
from ..schemas import ConceptCreate, ConceptEdgeCreate

router = APIRouter(prefix="/api/concepts", tags=["concepts"])

_RELATIONS = ("prerequisite", "part_of", "related")


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


# --- Structural knowledge graph (concepts + concept_edges tables) ------------

_TRAVERSAL_SQL = text(
    # Both directions from the root, each to `depth` hops: prerequisites
    # the root builds on, and everything the root unlocks. Two recursive
    # CTEs because Postgres allows a single recursive term per CTE.
    """
    WITH RECURSIVE
    down AS (
        SELECT c.id AS id, 0 AS depth
        FROM concepts c
        WHERE c.user_id = :uid AND lower(c.name) = lower(:root)
        UNION ALL
        SELECT e.target_id, d.depth + 1
        FROM down d JOIN concept_edges e ON e.source_id = d.id
        WHERE d.depth < :maxdepth
    ),
    up AS (
        SELECT c.id AS id, 0 AS depth
        FROM concepts c
        WHERE c.user_id = :uid AND lower(c.name) = lower(:root)
        UNION ALL
        SELECT e.source_id, u.depth + 1
        FROM up u JOIN concept_edges e ON e.target_id = u.id
        WHERE u.depth < :maxdepth
    )
    SELECT DISTINCT c.id FROM concepts c
    JOIN (SELECT id FROM down UNION SELECT id FROM up) w ON w.id = c.id
    """
)


def _node(name: str, mastery: dict, row: Concept | None) -> dict[str, Any]:
    entry = mastery.get(name, {})
    fsrs = entry.get("fsrs")
    return {
        "id": row.id if row is not None else None,
        "name": row.name if row is not None else name,
        # "graph": a concepts-table row; "memory": mastery-store only.
        "source": "graph" if row is not None else "memory",
        "description": row.description if row is not None else "",
        "module_id": row.module_id if row is not None else None,
        "mastery_pct": entry.get("mastery_pct"),
        "seen": entry.get("seen", 0),
        "due": fsrs_scheduler.is_due(fsrs),
        "due_in_days": fsrs_scheduler.due_in_days(fsrs),
        "retrievability": fsrs_scheduler.retrievability(fsrs),
        "stability": (fsrs or {}).get("stability"),
    }


@router.get("/graph")
async def concept_graph(
    root: str | None = None,
    depth: int = 2,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """The structural knowledge graph: nodes + typed edges.

    Merges two sources — the concepts/concept_edges tables (canonical,
    id-keyed, LLM-extraction-ready) and the mastery store (name-keyed,
    populated automatically by quizzes/flashcards) — so the graph works
    from day one. With ?root=<name>&depth=N the table portion is limited
    to a recursive traversal around that concept (both prerequisite
    directions); a root that only exists in memory gets its one-hop
    mastery neighborhood.
    """
    depth = max(0, min(depth, 6))
    mastery = await memory_store.get_concept_mastery(session)

    rows: list[Concept]
    if root:
        ids = (
            await session.execute(
                _TRAVERSAL_SQL, {"uid": user, "root": root, "maxdepth": depth}
            )
        ).scalars().all()
        if not ids and root.strip().lower() in {m.lower() for m in mastery}:
            # Root only the mastery store knows — one-hop memory graph.
            return _memory_neighborhood(root, mastery)
        if not ids:
            raise HTTPException(status_code=404, detail="Concept not found")
        rows = list(
            (
                await session.execute(
                    select(Concept).where(Concept.id.in_(ids), Concept.user_id == user)
                )
            )
            .scalars()
            .all()
        )
    else:
        rows = list(
            (
                await session.execute(select(Concept).where(Concept.user_id == user))
            )
            .scalars()
            .all()
        )

    by_name = {r.name: r for r in rows}
    row_ids = {r.id for r in rows}

    edges_result = await session.execute(
        select(ConceptEdge).where(ConceptEdge.source_id.in_(row_ids))
    )
    seen_edge_keys: set[tuple[str, str, str]] = set()
    edges: list[dict[str, Any]] = []
    for e in edges_result.scalars().all():
        source_name = next((r.name for r in rows if r.id == e.source_id), None)
        target_name = next((r.name for r in rows if r.id == e.target_id), None)
        if not source_name or not target_name:
            continue  # edge dangling outside the selected subgraph
        key = (source_name.lower(), target_name.lower(), e.relation)
        seen_edge_keys.add(key)
        edges.append(
            {"source": source_name, "target": target_name, "relation": e.relation, "origin": "table"}
        )

    # Mastery-only nodes (and their prerequisite/related edges) join the
    # party unless a root-limited traversal excluded them.
    nodes: list[dict[str, Any]] = [_node(r.name, mastery, r) for r in rows]
    if not root:
        for name in mastery:
            if name not in by_name:
                nodes.append(_node(name, mastery, None))
                entry = mastery.get(name, {})
                for p in entry.get("prerequisites") or []:
                    key = (name.lower(), str(p).lower(), "prerequisite")
                    if key not in seen_edge_keys:
                        seen_edge_keys.add(key)
                        edges.append({"source": name, "target": str(p), "relation": "prerequisite", "origin": "memory"})
                for r in entry.get("related") or []:
                    key = (name.lower(), str(r).lower(), "related")
                    if key not in seen_edge_keys:
                        seen_edge_keys.add(key)
                        edges.append({"source": name, "target": str(r), "relation": "related", "origin": "memory"})

    return {"root": root, "depth": depth, "nodes": nodes, "edges": edges}


def _memory_neighborhood(root: str, mastery: dict) -> dict[str, Any]:
    """One-hop graph around a mastery-store-only concept name."""
    target = next(
        (n for n in mastery if n.strip().lower() == root.strip().lower()), None
    )
    nodes = [_node(target, mastery, None)]
    edges: list[dict[str, Any]] = []
    entry = mastery.get(target, {})
    for p in entry.get("prerequisites") or []:
        nodes.append(_node(str(p), mastery, None))
        edges.append({"source": target, "target": str(p), "relation": "prerequisite", "origin": "memory"})
    for r in entry.get("related") or []:
        nodes.append(_node(str(r), mastery, None))
        edges.append({"source": target, "target": str(r), "relation": "related", "origin": "memory"})
    return {"root": target, "depth": 1, "nodes": nodes, "edges": edges}


@router.post("", status_code=201)
async def create_concept(
    req: ConceptCreate,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Create a concept in the knowledge graph (409 if the name exists)."""
    existing = await session.execute(
        select(Concept).where(Concept.user_id == user, Concept.name == req.name.strip())
    )
    if existing.first() is not None:
        raise HTTPException(status_code=409, detail="Concept already exists")
    if req.module_id is not None:
        if await session.get(Module, req.module_id) is None:
            raise HTTPException(status_code=404, detail="Module not found")
    concept = Concept(
        id=uuid.uuid4().hex[:12],
        user_id=user,
        name=req.name.strip(),
        description=req.description,
        module_id=req.module_id,
    )
    session.add(concept)
    await session.commit()
    await session.refresh(concept)
    return concept


@router.post("/{concept_id}/edges", status_code=201)
async def create_concept_edge(
    concept_id: str,
    req: ConceptEdgeCreate,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Add a typed edge from one of the user's concepts to a target named
    concept (created on the fly if the target isn't in the graph yet)."""
    source = await session.get(Concept, concept_id)
    if source is None or source.user_id != user:
        raise HTTPException(status_code=404, detail="Concept not found")

    target_name = req.target.strip()
    target = (
        await session.execute(
            select(Concept).where(Concept.user_id == user, Concept.name == target_name)
        )
    ).scalar_one_or_none()
    if target is None:
        target = Concept(
            id=uuid.uuid4().hex[:12], user_id=user, name=target_name, description=""
        )
        session.add(target)

    dup = await session.execute(
        select(ConceptEdge).where(
            ConceptEdge.source_id == source.id,
            ConceptEdge.target_id == target.id,
            ConceptEdge.relation == req.relation,
        )
    )
    if dup.first() is not None:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Edge already exists")

    edge = ConceptEdge(
        id=uuid.uuid4().hex[:12],
        source_id=source.id,
        target_id=target.id,
        relation=req.relation,
    )
    session.add(edge)
    await session.commit()
    await session.refresh(edge)
    return {
        "id": edge.id,
        "source": source.name,
        "target": target.name,
        "relation": edge.relation,
    }


@router.delete("/edges/{edge_id}", status_code=204)
async def delete_concept_edge(
    edge_id: str,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    """Remove one of the user's edges (404 if not theirs)."""
    edge = (
        await session.execute(
            select(ConceptEdge).join(Concept, ConceptEdge.source_id == Concept.id).where(
                ConceptEdge.id == edge_id, Concept.user_id == user
            )
        )
    ).scalar_one_or_none()
    if edge is None:
        raise HTTPException(status_code=404, detail="Edge not found")
    await session.delete(edge)
    await session.commit()
