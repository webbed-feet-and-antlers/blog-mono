"""Generation route — runs the LangGraph agent to produce notes/quiz/flashcards."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.graph import run_generation
from ..db import get_session
from ..models import ContentItem, Document
from ..schemas import ContentItemOut, GenerateRequest

router = APIRouter(prefix="/api/generate", tags=["generate"])
logger = logging.getLogger(__name__)


@router.post("", response_model=ContentItemOut, status_code=201)
async def generate(
    req: GenerateRequest, session: AsyncSession = Depends(get_session)
):
    doc = await session.get(Document, req.document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        final_state = await run_generation(
            document_id=doc.id,
            document_text=doc.text,
            task_type=req.task_type,
            session=session,
            instructions=req.instructions,
        )
    except Exception as exc:
        logger.exception("Agent generation failed")
        raise HTTPException(status_code=502, detail=f"Agent failed: {exc}")

    if final_state.get("error"):
        raise HTTPException(status_code=422, detail=final_state["error"])

    item = final_state.get("content_item")
    if not item:
        raise HTTPException(status_code=500, detail="Agent produced no content")

    # Commit the ContentItem + memory writes the agent staged on the session.
    await session.commit()

    # Re-fetch the persisted ContentItem so the response includes server-set
    # fields (created_at) the agent's in-memory dict doesn't carry.
    saved = await session.get(ContentItem, item["id"])
    return saved
