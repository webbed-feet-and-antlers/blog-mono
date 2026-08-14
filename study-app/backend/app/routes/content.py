"""CRUD routes for generated content items (notes/quiz/flashcards)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_session
from ..models import ContentItem
from ..schemas import ContentItemOut

router = APIRouter(prefix="/api/content", tags=["content"])


@router.get("", response_model=list[ContentItemOut])
async def list_content(
    document_id: str | None = Query(default=None),
    type: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    # Eager-load the parent document so callers can show which doc each item
    # belongs to without a second round-trip.
    stmt = (
        select(ContentItem)
        .options(selectinload(ContentItem.document))
        .order_by(ContentItem.created_at.desc())
    )
    if document_id is not None:
        stmt = stmt.where(ContentItem.document_id == document_id)
    if type is not None:
        stmt = stmt.where(ContentItem.type == type)
    result = await session.execute(stmt)
    return result.scalars().all()


@router.get("/{content_id}", response_model=ContentItemOut)
async def get_content(content_id: str, session: AsyncSession = Depends(get_session)):
    item = await session.get(ContentItem, content_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Content item not found")
    return item


@router.delete("/{content_id}", status_code=204)
async def delete_content(
    content_id: str, session: AsyncSession = Depends(get_session)
):
    item = await session.get(ContentItem, content_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Content item not found")
    await session.delete(item)
    await session.commit()
