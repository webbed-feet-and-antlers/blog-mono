"""Lecture session routes — CRUD + slide image rendering.

A LectureSession groups a recording (audio Document), slides (PDF Document),
user-authored notes, and a slide↔audio timestamp mapping. The dedicated
recording page creates sessions; the lecture view plays them back with
auto-advancing slides.
"""

from __future__ import annotations

import io
import uuid

import fitz  # PyMuPDF
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Document, LectureSession
from ..schemas import (
    LectureSessionCreate,
    LectureSessionDetail,
    LectureSessionNotesUpdate,
    LectureSessionOut,
)

router = APIRouter(prefix="/api/lectures", tags=["lectures"])


@router.post("", response_model=LectureSessionOut, status_code=201)
async def create_lecture_session(
    req: LectureSessionCreate, session: AsyncSession = Depends(get_session)
):
    lecture = LectureSession(
        id=uuid.uuid4().hex[:12],
        lesson_id=req.lesson_id,
        title=req.title,
        audio_doc_id=req.audio_doc_id,
        slides_doc_id=req.slides_doc_id,
        notes=req.notes,
        duration_seconds=req.duration_seconds,
        slide_timestamps=[t.model_dump() for t in req.slide_timestamps],
        slide_count=req.slide_count,
        status="completed",
    )
    session.add(lecture)
    await session.commit()
    await session.refresh(lecture)
    return lecture


@router.get("", response_model=list[LectureSessionOut])
async def list_lectures(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(LectureSession).order_by(LectureSession.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{lecture_id}", response_model=LectureSessionDetail)
async def get_lecture(
    lecture_id: str, session: AsyncSession = Depends(get_session)
):
    lecture = await session.get(LectureSession, lecture_id)
    if lecture is None:
        raise HTTPException(status_code=404, detail="Lecture session not found")

    # Eager-load the audio and slides documents.
    audio_doc = None
    slides_doc = None
    if lecture.audio_doc_id:
        audio_doc = await session.get(Document, lecture.audio_doc_id)
    if lecture.slides_doc_id:
        slides_doc = await session.get(Document, lecture.slides_doc_id)

    data = {c.name: getattr(lecture, c.name) for c in lecture.__table__.columns}
    data["audio_doc"] = audio_doc
    data["slides_doc"] = slides_doc
    return data


@router.patch("/{lecture_id}/notes", response_model=LectureSessionOut)
async def update_notes(
    lecture_id: str,
    req: LectureSessionNotesUpdate,
    session: AsyncSession = Depends(get_session),
):
    lecture = await session.get(LectureSession, lecture_id)
    if lecture is None:
        raise HTTPException(status_code=404, detail="Lecture session not found")
    lecture.notes = req.notes
    await session.commit()
    await session.refresh(lecture)
    return lecture


@router.get("/{lecture_id}/slides/{page}")
async def get_slide_image(
    lecture_id: str,
    page: int,
    session: AsyncSession = Depends(get_session),
):
    """Render a specific PDF page (slide) as a PNG image.

    page is 1-indexed (page=1 is the first slide).
    """
    lecture = await session.get(LectureSession, lecture_id)
    if lecture is None:
        raise HTTPException(status_code=404, detail="Lecture session not found")
    if lecture.slides_doc_id is None:
        raise HTTPException(status_code=404, detail="No slides uploaded")

    slides_doc = await session.get(Document, lecture.slides_doc_id)
    if slides_doc is None:
        raise HTTPException(status_code=404, detail="Slides document not found")

    pdf_path = slides_doc.file_path
    try:
        doc = fitz.open(pdf_path)
        if page < 1 or page > doc.page_count:
            doc.close()
            raise HTTPException(
                status_code=404,
                detail=f"Page {page} out of range (1-{doc.page_count})",
            )
        # Render the page to a PNG at a reasonable resolution.
        fitz_page = doc[page - 1]  # 0-indexed
        mat = fitz.Matrix(2, 2)  # 2x zoom for clarity
        pix = fitz_page.get_pixmap(matrix=mat)
        png_bytes = pix.tobytes("png")
        doc.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to render slide: {exc}"
        )

    return Response(content=png_bytes, media_type="image/png")
