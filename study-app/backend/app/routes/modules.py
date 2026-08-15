"""Module/Lesson CRUD + hierarchy tree endpoint.

Module → Lesson → Documents. The hierarchy is optional — documents with
lesson_id=NULL are "unfiled" and render in their own section.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_session
from ..models import Document, Lesson, Module
from ..schemas import (
    DocumentMove,
    DocumentOut,
    LessonCreate,
    LessonOut,
    LessonWithDocs,
    ModuleCreate,
    ModuleUpdate,
    ModuleOut,
    ModuleTreeResponse,
    ModuleWithTree,
)

router = APIRouter(prefix="/api", tags=["modules"])


# --- Tree ---


@router.get("/modules", response_model=ModuleTreeResponse)
async def get_module_tree(session: AsyncSession = Depends(get_session)):
    """Return the full hierarchy: modules → lessons → documents, plus unfiled docs."""
    from ..agent.memory import get_doc_topics

    # Load document topics from cached analyses (one query for all docs).
    topics = await get_doc_topics(session)

    # Load modules with their lessons + documents eager-loaded.
    result = await session.execute(
        select(Module)
        .options(
            selectinload(Module.lessons).selectinload(Lesson.documents),
            selectinload(Module.documents),
        )
        .order_by(Module.created_at)
    )
    modules = result.scalars().unique().all()

    # Load unfiled documents (neither lesson_id nor module_id set).
    unfiled_result = await session.execute(
        select(Document)
        .where(
            Document.lesson_id.is_(None),
            Document.module_id.is_(None),
        )
        .order_by(Document.uploaded_at.desc())
    )
    unfiled = unfiled_result.scalars().all()

    return ModuleTreeResponse(
        modules=[
            ModuleWithTree(
                id=m.id,
                title=m.title,
                created_at=m.created_at,
                lessons=[
                    LessonWithDocs(
                        id=les.id,
                        title=les.title,
                        module_id=les.module_id,
                        created_at=les.created_at,
                        documents=[
                            DocumentOut(
                                id=d.id,
                                filename=d.filename,
                                mime=d.mime,
                                page_count=d.page_count,
                                char_count=d.char_count,
                                uploaded_at=d.uploaded_at,
                                lesson_id=d.lesson_id,
                                module_id=d.module_id,
                                kind=d.kind,
                                duration_seconds=d.duration_seconds,
                                transcription_status=d.transcription_status,
                                topic=topics.get(d.id),
                            )
                            for d in sorted(
                                les.documents, key=lambda x: x.uploaded_at
                            )
                        ],
                    )
                    for les in sorted(m.lessons, key=lambda x: x.created_at)
                ],
                documents=[
                    DocumentOut(
                        id=d.id,
                        filename=d.filename,
                        mime=d.mime,
                        page_count=d.page_count,
                        char_count=d.char_count,
                        uploaded_at=d.uploaded_at,
                        lesson_id=d.lesson_id,
                        module_id=d.module_id,
                        kind=d.kind,
                        duration_seconds=d.duration_seconds,
                        transcription_status=d.transcription_status,
                        topic=topics.get(d.id),
                    )
                    for d in sorted(m.documents, key=lambda x: x.uploaded_at)
                ],
            )
            for m in modules
        ],
        unfiled=[
            DocumentOut(
                id=d.id,
                filename=d.filename,
                mime=d.mime,
                page_count=d.page_count,
                char_count=d.char_count,
                uploaded_at=d.uploaded_at,
                lesson_id=d.lesson_id,
                module_id=d.module_id,
                kind=d.kind,
                duration_seconds=d.duration_seconds,
                transcription_status=d.transcription_status,
                topic=topics.get(d.id),
            )
            for d in unfiled
        ],
    )


# --- Module CRUD ---


@router.post("/modules", response_model=ModuleOut, status_code=201)
async def create_module(
    req: ModuleCreate, session: AsyncSession = Depends(get_session)
):
    module = Module(id=uuid.uuid4().hex[:12], title=req.title, exam_date=req.exam_date)
    session.add(module)
    await session.commit()
    await session.refresh(module)
    return module


@router.patch("/modules/{module_id}", response_model=ModuleOut)
async def update_module(
    module_id: str,
    req: ModuleUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Update a module's title and/or exam date (paces its study plan).
    Only fields actually present in the request body change."""
    module = await session.get(Module, module_id)
    if module is None:
        raise HTTPException(status_code=404, detail="Module not found")
    if "title" in req.model_fields_set and req.title is not None:
        module.title = req.title
    if "exam_date" in req.model_fields_set:
        module.exam_date = req.exam_date
    await session.commit()
    await session.refresh(module)
    return module


@router.delete("/modules/{module_id}", status_code=204)
async def delete_module(
    module_id: str, session: AsyncSession = Depends(get_session)
):
    module = await session.get(Module, module_id)
    if module is None:
        raise HTTPException(status_code=404, detail="Module not found")
    await session.delete(module)  # cascades to lessons; docs → unfiled (SET NULL)
    await session.commit()


# --- Lesson CRUD ---


@router.post(
    "/modules/{module_id}/lessons", response_model=LessonOut, status_code=201
)
async def create_lesson(
    module_id: str,
    req: LessonCreate,
    session: AsyncSession = Depends(get_session),
):
    module = await session.get(Module, module_id)
    if module is None:
        raise HTTPException(status_code=404, detail="Module not found")
    lesson = Lesson(
        id=uuid.uuid4().hex[:12], module_id=module_id, title=req.title
    )
    session.add(lesson)
    await session.commit()
    await session.refresh(lesson)
    return lesson


@router.patch("/lessons/{lesson_id}", response_model=LessonOut)
async def rename_lesson(
    lesson_id: str,
    req: LessonCreate,
    session: AsyncSession = Depends(get_session),
):
    lesson = await session.get(Lesson, lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    lesson.title = req.title
    await session.commit()
    await session.refresh(lesson)
    return lesson


@router.delete("/lessons/{lesson_id}", status_code=204)
async def delete_lesson(
    lesson_id: str, session: AsyncSession = Depends(get_session)
):
    lesson = await session.get(Lesson, lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    await session.delete(lesson)  # docs → unfiled (SET NULL)
    await session.commit()


# --- Document filing ---


@router.patch("/documents/{document_id}", response_model=DocumentOut)
async def move_document(
    document_id: str,
    req: DocumentMove,
    session: AsyncSession = Depends(get_session),
):
    """Move a document into a lesson, a module, or unfile it.

    Mutual exclusivity: setting lesson_id clears module_id and vice versa.
    Both null = unfiled.
    """
    from ..models import Document as Doc

    doc = await session.get(Doc, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    if req.lesson_id is not None:
        lesson = await session.get(Lesson, req.lesson_id)
        if lesson is None:
            raise HTTPException(status_code=404, detail="Lesson not found")
        doc.lesson_id = req.lesson_id
        doc.module_id = None  # mutual exclusivity
    elif req.module_id is not None:
        module = await session.get(Module, req.module_id)
        if module is None:
            raise HTTPException(status_code=404, detail="Module not found")
        doc.module_id = req.module_id
        doc.lesson_id = None  # mutual exclusivity
    else:
        # Both null → unfile.
        doc.lesson_id = None
        doc.module_id = None

    await session.commit()
    await session.refresh(doc)
    return doc
