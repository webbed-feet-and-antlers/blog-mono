"""Generation route — runs the LangGraph agent to produce notes/quiz/flashcards."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.graph import run_generation, run_generation_streamed
from ..auth import current_user_id, get_current_user
from ..db import get_session
from ..events import bus
from ..events.domain import GenerationCompleted
from ..models import ContentItem, Document
from ..schemas import ContentItemOut, GenerateRequest

router = APIRouter(prefix="/api/generate", tags=["generate"])
logger = logging.getLogger(__name__)


@router.post("", response_model=ContentItemOut, status_code=201)
async def generate(
    req: GenerateRequest,
    session: AsyncSession = Depends(get_session),
    user: str = Depends(get_current_user),
):
    doc = await session.get(Document, req.document_id)
    if doc is None or doc.user_id != user:
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

    # Post-commit reactions (recommendation session tracking) run on the bus.
    await bus.publish(
        GenerationCompleted(
            document_id=doc.id,
            content_id=item["id"],
            task_type=req.task_type,
        )
    )
    return saved


@router.post("/stream")
async def generate_stream(
    req: GenerateRequest, user: str = Depends(get_current_user)
):
    """Stream generation progress as SSE events.

    Emits `status` events with a friendly message as each agent node completes,
    then a final `done` event with the persisted ContentItem (or `error`).

    The frontend reads this via fetch() + ReadableStream (not EventSource,
    because this is a POST request).
    """
    from ..db import SessionLocal

    async def event_stream():
        # Own our session — the generator outlives the request dependency
        # scope, and re-establish the owner's identity inside it (memory
        # writes during generation resolve the user from the contextvar).
        current_user_id.set(user)
        async with SessionLocal() as session:
            doc = await session.get(Document, req.document_id)
            if doc is None or doc.user_id != user:
                yield _sse("error", {"message": "Document not found"})
                return

            # Persist the hint as a profile signal — if the user keeps asking
            # for "concise notes" or "10 questions", the profile learns it.
            if req.instructions and req.instructions.strip():
                from ..agent import memory as memory_store

                await memory_store.update_learner_profile(
                    session, hint=req.instructions.strip()
                )
                await session.commit()

            try:
                final_state = None
                async for status, state in run_generation_streamed(
                    document_id=doc.id,
                    document_text=doc.text,
                    task_type=req.task_type,
                    session=session,
                    instructions=req.instructions,
                ):
                    final_state = state
                    if status == "error":
                        yield _sse("error", {
                            "message": state.get("error", "Agent failed")
                        })
                        return
                    if status == "done":
                        break
                    yield _sse("status", {"status": status})

                if final_state and final_state.get("error"):
                    yield _sse("error", {"message": final_state["error"]})
                    return

                item = (final_state or {}).get("content_item")
                if not item:
                    yield _sse("error", {"message": "Agent produced no content"})
                    return

                # Commit the agent's writes.
                await session.commit()
                saved = await session.get(ContentItem, item["id"])

                # Post-commit reactions (session tracking) run on the bus.
                await bus.publish(
                    GenerationCompleted(
                        document_id=doc.id,
                        content_id=item["id"],
                        task_type=req.task_type,
                    )
                )
                yield _sse("done", {
                    "status": "done",
                    "item": _content_item_dict(saved),
                })
            except Exception as exc:
                logger.exception("Streaming generation failed")
                yield _sse("error", {"message": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable proxy buffering
        },
    )


def _sse(event: str, data: dict) -> str:
    """Format a Server-Sent Event string."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _content_item_dict(item) -> dict:
    """Serialize a ContentItem ORM object to a JSON-safe dict."""
    return {
        "id": item.id,
        "document_id": item.document_id,
        "type": item.type,
        "content": item.content,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }
