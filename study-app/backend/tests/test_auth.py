"""Auth + multi-user isolation tests.

Clerk verifies real session JWTs; tests bypass it by overriding the
get_current_user dependency (see conftest). These tests pin the contract
the override stands in for: unauthenticated requests 401, and no user's
data is ever visible to another.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import current_user_id, get_current_user
from app.main import app as fastapi_app
from app.models import ContentItem, Document


def _auth_as(uid: str):
    async def _override() -> str:
        current_user_id.set(uid)
        return uid

    return _override


@asynccontextmanager
async def client_as(uid: str):
    """An HTTP client authenticated as `uid` (sequential use only — the
    override lives on the shared app object)."""
    fastapi_app.dependency_overrides[get_current_user] = _auth_as(uid)
    try:
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        fastapi_app.dependency_overrides.pop(get_current_user, None)


def _doc(doc_id: str, uid: str) -> Document:
    return Document(
        id=doc_id, user_id=uid, filename=f"{doc_id}.pdf",
        mime="application/pdf", file_path="/tmp/x.pdf",
        text="t", kind="text",
    )


async def test_endpoints_require_auth(anon_client):
    """Without a valid session, every API endpoint returns 401 (not 500,
    not a leak)."""
    targets = [
        anon_client.get("/api/documents"),
        anon_client.get("/api/content"),
        anon_client.get("/api/modules"),
        anon_client.get("/api/concepts"),
        anon_client.get("/api/recommend"),
        anon_client.get("/api/events"),
        anon_client.get("/api/memory/profile"),
        anon_client.post("/api/activity", content=b"{}"),
        anon_client.post(
            "/api/documents",
            files={"file": ("a.txt", b"hello", "text/plain")},
        ),
    ]
    for coro in targets:
        resp = await coro
        assert resp.status_code == 401, (resp.url, resp.status_code)


async def test_two_user_document_isolation(db):
    db.add_all([_doc("doc-iso-a", "userA"), _doc("doc-iso-b", "userB")])
    await db.commit()

    async with client_as("userA") as a:
        resp = await a.get("/api/documents")
        assert [d["id"] for d in resp.json()] == ["doc-iso-a"]

        # Cross-user ids 404 — existence is not disclosed.
        assert (await a.get("/api/documents/doc-iso-b")).status_code == 404
        assert (await a.delete("/api/documents/doc-iso-b")).status_code == 404

    async with client_as("userB") as b:
        resp = await b.get("/api/documents")
        assert [d["id"] for d in resp.json()] == ["doc-iso-b"]
        assert (await b.get("/api/documents/doc-iso-a")).status_code == 404


async def test_two_user_content_isolation(db):
    for uid in ("userA", "userB"):
        db.add_all([
            _doc(f"docc-{uid}", uid),
            ContentItem(
                id=f"quiz-{uid}", user_id=uid, document_id=f"docc-{uid}",
                type="quiz", content={"title": uid, "questions": []},
            ),
        ])
    await db.commit()

    async with client_as("userA") as a:
        items = (await a.get("/api/content")).json()
        assert [i["id"] for i in items] == ["quiz-userA"]

    # Cross-user quiz submit 404s (no attempt recorded).
    async with client_as("userB") as b:
        resp = await b.post("/api/quiz/quiz-userA/attempt", json={"answers": {}})
        assert resp.status_code == 404


async def test_two_user_memory_isolation(db):
    """The learner profile (and every user-scope memory blob) is per-user:
    each request context resolves its own memory."""
    from app.agent import memory as memory_store
    from app.auth import user_scope

    for uid, score in (("userA", 0.42), ("userB", 0.93)):
        with user_scope(uid):
            await memory_store.update_learner_profile(
                db, quiz_score=score, doc_difficulty="medium"
            )
    await db.commit()

    async with client_as("userA") as a:
        prof = (await a.get("/api/memory/profile")).json()
        assert prof["stats"]["avg_score"] == pytest.approx(0.42)

    async with client_as("userB") as b:
        prof = (await b.get("/api/memory/profile")).json()
        assert prof["stats"]["avg_score"] == pytest.approx(0.93)


async def test_concept_mastery_isolated(db):
    from app.agent import memory as memory_store
    from app.auth import user_scope

    with user_scope("userA"):
        await memory_store.update_concept_mastery(db, "mitosis", correct=False)
    with user_scope("userB"):
        await memory_store.update_concept_mastery(db, "mitosis", correct=True)
    await db.commit()

    async with client_as("userA") as a:
        resp = await a.get("/api/concepts")
        entry = {c["concept"]: c for c in resp.json()}["mitosis"]
        assert entry["seen"] == 1 and entry["correct"] == 0

    async with client_as("userB") as b:
        resp = await b.get("/api/concepts")
        entry = {c["concept"]: c for c in resp.json()}["mitosis"]
        assert entry["seen"] == 1 and entry["correct"] == 1
