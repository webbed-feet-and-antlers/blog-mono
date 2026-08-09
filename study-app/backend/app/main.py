"""FastAPI app for the AI study tools backend.

Mirrors the structure of the repo's embeddings/server.py: a single app with an
asynccontextmanager lifespan that initializes resources, Pydantic schemas, and
thin route handlers that delegate to the agent layer.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import init_db
from .routes import content, documents, generate, memory, quiz

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Initializing database…")
    await init_db()
    logger.info("Study app backend ready")
    yield


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


app.include_router(documents.router)
app.include_router(generate.router)
app.include_router(content.router)
app.include_router(quiz.router)
app.include_router(memory.router)
