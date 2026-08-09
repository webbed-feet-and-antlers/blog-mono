"""Document upload/list/get/delete routes."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import storage
from ..db import get_session
from ..models import Document
from ..parsers import extract_text
from ..schemas import DocumentDetail, DocumentOut

router = APIRouter(prefix="/api/documents", tags=["documents"])

# Cap uploads at 25 MB for the POC.
MAX_BYTES = 25 * 1024 * 1024
ALLOWED_SUFFIXES = {".pdf", ".txt", ".md"}


@router.post("", response_model=DocumentOut, status_code=201)
async def upload_document(
    file: UploadFile, session: AsyncSession = Depends(get_session)
):
    filename = file.filename or "upload"
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{suffix}'. Allowed: {sorted(ALLOWED_SUFFIXES)}",
        )

    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status_code=413, detail="File exceeds 25 MB limit."
        )

    mime = file.content_type or mimetypes.guess_type(filename)[0] or "text/plain"

    file_id, dest = await storage.save_upload(filename, data)
    try:
        text, page_count = extract_text(dest, mime)
    except Exception as exc:  # pragma: no cover — parse failure
        await storage.delete_upload(str(dest))
        raise HTTPException(status_code=422, detail=f"Failed to parse file: {exc}")

    doc = Document(
        id=file_id,
        filename=filename,
        mime=mime,
        file_path=str(dest),
        text=text,
        page_count=page_count,
        char_count=len(text),
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    return doc


@router.get("", response_model=list[DocumentOut])
async def list_documents(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(Document).order_by(Document.uploaded_at.desc())
    )
    return result.scalars().all()


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: str, session: AsyncSession = Depends(get_session)):
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: str, session: AsyncSession = Depends(get_session)
):
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    await storage.delete_upload(doc.file_path)
    await session.delete(doc)
    await session.commit()
