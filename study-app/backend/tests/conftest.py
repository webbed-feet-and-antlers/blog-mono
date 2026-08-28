"""Shared test fixtures.

DB_PATH is set BEFORE any app import — the engine in app.db reads
settings.db_url at import time, so this must happen first. Each pytest
session gets a throwaway SQLite file.

Background features that would make real LLM/audio calls are disabled via
env (they default off anyway); the flag-gated handler paths are exercised
via monkeypatched settings in the tests that need them.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="study-app-tests-"))
os.environ["DB_PATH"] = str(_TMP / "test.db")
# Dummy Clerk key: token-less requests then fail verification with a clean
# 401 instead of the "secret not configured" 503 (no network is touched).
os.environ["CLERK_SECRET_KEY"] = "sk_test_dummy"
os.environ["AUTO_GENERATE_FLASHCARDS"] = "false"
os.environ["AUTO_RENAME_FILES"] = "false"
os.environ["PROACTIVE_ENABLED"] = "false"

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import get_current_user
from app.db import SessionLocal, init_db
from app.main import app as fastapi_app
from app.models import ContentItem, Document


@pytest.fixture(autouse=True)
async def _ready_db():
    """Ensure tables exist (ASGITransport doesn't run the app lifespan)."""
    await init_db()
    yield


@pytest.fixture
async def client():
    """HTTP client authenticated as the ambient default user ("").

    get_current_user normally verifies a Clerk session JWT and sets the
    request identity; tests bypass Clerk by overriding it. Returning ""
    keeps HTTP-driven writes and direct-call assertions on the same
    identity (the ambient user every non-request context sees).
    """

    async def _ambient_user() -> str:
        return ""

    fastapi_app.dependency_overrides[get_current_user] = _ambient_user
    try:
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        fastapi_app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
async def anon_client():
    """HTTP client with NO auth override — endpoints must 401."""
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def db():
    """A direct DB session for seeding + asserting."""
    async with SessionLocal() as session:
        yield session


def make_quiz(
    doc_id: str = "doc-quiz",
    content_id: str = "quiz-1",
    concepts: tuple[str, str] = ("Photosynthesis", "Calvin cycle"),
) -> tuple[Document, ContentItem]:
    """A document + 2-question quiz whose questions carry concept tags."""
    doc = Document(
        id=doc_id,
        filename="bio-notes.pdf",
        mime="application/pdf",
        file_path="/tmp/bio-notes.pdf",
        text="Photosynthesis and the Calvin cycle…",
        kind="text",
    )
    quiz = ContentItem(
        id=content_id,
        document_id=doc_id,
        type="quiz",
        content={
            "title": "Photosynthesis quiz",
            "questions": [
                {
                    "id": "q1",
                    "prompt": f"What is {concepts[0]}?",
                    "options": ["Right", "Wrong", "Wrong", "Wrong"],
                    "answer_idx": 0,
                    "explanation": "",
                    "concept": concepts[0],
                },
                {
                    "id": "q2",
                    "prompt": f"Where does the {concepts[1]} occur?",
                    "options": ["Wrong", "Right", "Wrong", "Wrong"],
                    "answer_idx": 1,
                    "explanation": "",
                    "concept": concepts[1],
                },
            ],
        },
    )
    return doc, quiz
