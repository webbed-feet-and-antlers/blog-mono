"""Knowledge-graph endpoints: concepts/concept_edges CRUD + /graph view.

The /graph merge (table rows + mastery-store names) and the recursive
traversal are the load-bearing parts — they must work on the same day the
tables are empty (mastery store only) and once extraction populates them.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from app.agent import memory as memory_store
from app.db import SessionLocal
from app.models import AgentMemory, Concept, ConceptEdge


@pytest.fixture(autouse=True)
async def _clean_graph_state(db):
    """Deterministic state: wipe the graph tables and the ambient user's
    concept_mastery blob before/after each test — the suite shares one DB
    file, and mastery entries we create here otherwise change planner
    behavior in later files (weak-first ordering reads mastery)."""
    await _wipe(db)
    yield
    await _wipe(db)


async def _wipe(db):
    await db.execute(delete(ConceptEdge))
    await db.execute(delete(Concept))
    await db.execute(
        delete(AgentMemory).where(AgentMemory.key == "concept_mastery")
    )
    await db.commit()


async def _seed_chain(db) -> dict[str, str]:
    """Three concepts: Cell → (prereq) Photosynthesis → (prereq) Calvin cycle."""
    ids = {}
    for name in ("Cell biology", "Photosynthesis", "Calvin cycle"):
        c = Concept(id=name.lower().replace(" ", "-"), user_id="", name=name)
        db.add(c)
        ids[name] = c.id
    db.add(
        ConceptEdge(
            id="e1", source_id=ids["Photosynthesis"], target_id=ids["Cell biology"], relation="prerequisite"
        )
    )
    db.add(
        ConceptEdge(
            id="e2", source_id=ids["Calvin cycle"], target_id=ids["Photosynthesis"], relation="prerequisite"
        )
    )
    await db.commit()
    return ids


async def test_graph_empty_tables_mastery_only(client, db):
    """No concept rows yet — mastery names still render as memory nodes."""
    async with SessionLocal() as s:
        await memory_store.update_concept_mastery(s, "Enzymes", correct=True)
        await s.commit()

    res = await client.get("/api/concepts/graph")
    assert res.status_code == 200
    body = res.json()
    names = {n["name"] for n in body["nodes"]}
    assert "Enzymes" in names
    enzymes = next(n for n in body["nodes"] if n["name"] == "Enzymes")
    assert enzymes["source"] == "memory"
    assert enzymes["mastery_pct"] == 1.0


async def test_graph_full_merge(client, db):
    """Table concepts render as graph nodes; mastery enriches them."""
    ids = await _seed_chain(db)
    async with SessionLocal() as s:
        await memory_store.update_concept_mastery(s, "Photosynthesis", correct=False)
        await s.commit()

    res = await client.get("/api/concepts/graph")
    assert res.status_code == 200
    body = res.json()
    by_name = {n["name"]: n for n in body["nodes"]}
    assert set(by_name) >= {"Cell biology", "Photosynthesis", "Calvin cycle"}
    assert by_name["Photosynthesis"]["source"] == "graph"
    assert by_name["Photosynthesis"]["id"] == ids["Photosynthesis"]
    assert by_name["Photosynthesis"]["mastery_pct"] == 0.0

    table_edges = [e for e in body["edges"] if e["origin"] == "table"]
    assert len(table_edges) == 2
    assert all(e["relation"] == "prerequisite" for e in table_edges)


async def test_graph_root_traversal_both_directions(client, db):
    """Root=Photosynthesis reaches down (Calvin cycle depends on it) and
    up (Cell biology is its prerequisite) within depth."""
    await _seed_chain(db)

    res = await client.get("/api/concepts/graph", params={"root": "photosynthesis", "depth": 2})
    assert res.status_code == 200
    names = {n["name"] for n in res.json()["nodes"]}
    # depth 2 from Photosynthesis: prerequisites (Cell biology) and what
    # depends on it (Calvin cycle, via its prerequisite edge).
    assert names == {"Photosynthesis", "Cell biology", "Calvin cycle"}

    # depth 0: only the root itself.
    res = await client.get("/api/concepts/graph", params={"root": "photosynthesis", "depth": 0})
    assert {n["name"] for n in res.json()["nodes"]} == {"Photosynthesis"}

    res = await client.get("/api/concepts/graph", params={"root": "nope"})
    assert res.status_code == 404


async def test_concept_crud_and_edges(client, db):
    res = await client.post("/api/concepts", json={"name": "Glycolysis"})
    assert res.status_code == 201
    glyco_id = res.json()["id"]

    # Duplicate name → 409.
    res = await client.post("/api/concepts", json={"name": "Glycolysis"})
    assert res.status_code == 409

    # Edge to a concept that doesn't exist yet creates the target.
    res = await client.post(
        f"/api/concepts/{glyco_id}/edges",
        json={"target": "ATP", "relation": "related"},
    )
    assert res.status_code == 201
    edge = res.json()
    assert edge == {"id": edge["id"], "source": "Glycolysis", "target": "ATP", "relation": "related"}

    # Duplicate edge → 409.
    res = await client.post(
        f"/api/concepts/{glyco_id}/edges",
        json={"target": "ATP", "relation": "related"},
    )
    assert res.status_code == 409

    # Invalid relation → 422 (Literal).
    res = await client.post(
        f"/api/concepts/{glyco_id}/edges",
        json={"target": "ATP", "relation": "sibling"},
    )
    assert res.status_code == 422

    # Delete the edge.
    res = await client.delete(f"/api/concepts/edges/{edge['id']}")
    assert res.status_code == 204
    left = (await db.execute(ConceptEdge.__table__.select())).all()
    assert left == []

    # Unknown concept → 404.
    res = await client.post(
        "/api/concepts/zzz/edges", json={"target": "ATP", "relation": "related"}
    )
    assert res.status_code == 404
