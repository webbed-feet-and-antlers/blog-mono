"""FastAPI app for the AI study tools backend.

Mirrors the structure of the repo's embeddings/server.py: a single app with an
asynccontextmanager lifespan that initializes resources, Pydantic schemas, and
thin route handlers that delegate to the agent layer.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import init_db
from .events import handlers as event_handlers  # noqa: F401 — registers bus handlers
from .proactive import proactive_loop
from .routes import activity, content, documents, events, flashcards, generate, lectures, memory, modules, plans, quiz, recommend, concepts, study_session

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Initializing database…")
    await init_db()

    # Launch the proactive agent if enabled. Runs as a background task on the
    # shared event loop; cancelled cleanly on shutdown.
    proactive_task: asyncio.Task | None = None
    if settings.proactive_enabled:
        proactive_task = asyncio.create_task(proactive_loop())
        logger.info("Proactive agent enabled — background loop started")

    logger.info("Study app backend ready")
    yield

    if proactive_task is not None:
        proactive_task.cancel()
        try:
            await proactive_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Study App Backend", lifespan=lifespan)

# Allow the Vite dev server (default 5173) to call the API during local dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


# Every API router requires a verified Clerk session (see app/auth.py).
# Handlers that need the user id take `Depends(get_current_user)` again —
# FastAPI caches the dependency per request, so it runs once.
from .auth import get_current_user  # noqa: E402

for _router in (
    documents.router,
    generate.router,
    content.router,
    quiz.router,
    flashcards.router,
    memory.router,
    modules.router,
    recommend.router,
    concepts.router,
    lectures.router,
    study_session.router,
    events.router,
    activity.router,
    plans.router,
):
    app.include_router(_router, dependencies=[Depends(get_current_user)])
