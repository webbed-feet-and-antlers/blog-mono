"""Document upload/list/get/delete routes.

Handles three kinds of documents:
  - text/PDF/MD: extracted immediately (existing flow)
  - audio recordings: transcribed in the background via Whisper
Slides (PDF) use the existing text flow unchanged.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import storage
from ..config import settings
from ..db import get_session
from ..models import Document
from ..parsers import extract_text
from ..schemas import DocumentDetail, DocumentOut

router = APIRouter(prefix="/api/documents", tags=["documents"])

MAX_BYTES = 25 * 1024 * 1024
AUDIO_MAX_BYTES = settings.audio_max_bytes
ALLOWED_SUFFIXES = {".pdf", ".txt", ".md", ".webm", ".mp3", ".m4a", ".wav", ".ogg", ".flac"}
AUDIO_SUFFIXES = {".webm", ".mp3", ".m4a", ".wav", ".ogg", ".flac"}

# MIME types for serving audio files.
AUDIO_MIME = {
    ".webm": "audio/webm",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}


@router.post("", response_model=DocumentOut, status_code=201)
async def upload_document(
    file: UploadFile,
    lesson_id: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    filename = file.filename or "upload"
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{suffix}'. Allowed: {sorted(ALLOWED_SUFFIXES)}",
        )

    is_audio = suffix in AUDIO_SUFFIXES
    max_bytes = AUDIO_MAX_BYTES if is_audio else MAX_BYTES

    data = await file.read()
    if len(data) > max_bytes:
        limit_mb = max_bytes // (1024 * 1024)
        raise HTTPException(
            status_code=413, detail=f"File exceeds {limit_mb} MB limit."
        )

    mime = file.content_type or mimetypes.guess_type(filename)[0] or (
        AUDIO_MIME.get(suffix, "application/octet-stream") if is_audio else "text/plain"
    )

    file_id, dest = await storage.save_upload(filename, data)

    if is_audio:
        # Audio: save file, create doc with pending transcription, return immediately.
        doc = Document(
            id=file_id,
            filename=filename,
            mime=mime,
            file_path=str(dest),
            text="",
            page_count=0,
            char_count=0,
            lesson_id=lesson_id,
            kind="audio",
            transcription_status="pending",
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)

        # Trigger background transcription → concept analysis.
        import asyncio
        from ..transcription import transcribe_then_analyze

        asyncio.create_task(transcribe_then_analyze(doc.id))
        return doc

    # Text/PDF: existing flow — extract text immediately.
    try:
        text, page_count = extract_text(dest, mime)
    except Exception as exc:
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
        lesson_id=lesson_id,
        kind="text",
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    # Trigger background concept analysis.
    import asyncio
    from ..agent.concept_graph import analyze_concepts_background

    asyncio.create_task(analyze_concepts_background(doc.id))
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

    # Attach the topic from the cached analysis (if available) so the
    # frontend can display it as a subtitle.
    from ..agent.memory import read_memory

    analysis = await read_memory(session, "doc", document_id, "analysis")
    if analysis and isinstance(analysis, dict) and analysis.get("topic"):
        # Build a dict from the ORM object + add the topic field.
        data = {c.name: getattr(doc, c.name) for c in doc.__table__.columns}
        data["topic"] = analysis["topic"]
        return data
    return doc


@router.get("/{document_id}/file")
async def get_document_file(
    document_id: str, session: AsyncSession = Depends(get_session)
):
    """Serve the raw file (audio, PDF) with correct Content-Type + Range support.

    Essential for audio seeking — the <audio> element needs Accept-Ranges.
    """
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    file_path = Path(doc.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    suffix = file_path.suffix.lower()
    media_type = AUDIO_MIME.get(suffix) or mimetypes.guess_type(file_path.name)[0] or (
        "application/pdf" if suffix == ".pdf" else "application/octet-stream"
    )

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=doc.filename,
    )


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
