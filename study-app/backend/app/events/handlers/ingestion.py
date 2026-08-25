"""Background ingestion chain — everything that happens after a document lands.

Used to be one 85-line function (analyze_concepts_background) with nested
try/excepts and three commit points. Now it's a chain of events, each step
independently observable in the agent_events log:

    DocumentIngested (background)
      └─ transcribe (audio only) → auto-rename → LLM analysis → cache
           └─ publishes DocumentAnalyzed
                ├─ merge_graph_and_retitle   (inline: graph + lecture titles)
                └─ auto_generate_flashcards  (background, flag-gated)
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...agent import memory as memory_store
from ...agent import tools
from ...agent.concept_graph import merge_concept_graph
from ...config import settings
from ...models import ContentItem, Document, LectureSession
from .. import bus
from ..domain import DocumentAnalyzed, DocumentIngested

logger = logging.getLogger(__name__)


@bus.on(DocumentIngested, background=True)
async def ingest_document(event: DocumentIngested, session: AsyncSession) -> None:
    """Transcribe (if audio) → rename → analyze → cache → publish analyzed."""
    doc = await session.get(Document, event.document_id)
    if doc is None:
        logger.warning("[events] ingested doc %s not found", event.document_id)
        return

    # Audio: transcribe first so the analysis has text to work with.
    # transcribe_document commits its own status milestones (visible in the
    # UI while the ASR call runs).
    if doc.kind == "audio" and doc.transcription_status != "done":
        from ...transcription import transcribe_document

        if not await transcribe_document(session, doc):
            return  # failure already recorded on the doc

    # Auto-rename machine-generated filenames (hex hashes, IMG_1234,
    # recording-<ts>, …) to a clean descriptive title. Good names are
    # left untouched — the heuristic gate costs no LLM call. Runs BEFORE
    # the analysis (own commit) so a flaky analysis call can't block it.
    if settings.auto_rename_files:
        try:
            await _maybe_rename_document(session, doc)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("[events] auto-rename failed for doc %s", doc.id)

    logger.info("[events] analyzing doc %s (%s)", doc.id, doc.filename)
    analysis = await tools.analyze_document(doc.text)

    # Cache the analysis so the first Generate call is instant.
    await memory_store.write_memory(session, "doc", doc.id, "analysis", analysis)

    # Commit BEFORE publishing the nested event — the bus rule is "no open
    # write transaction while a nested dispatch row writes from its own
    # session". This also preserves the old boundary: analysis is durable
    # even if the downstream chain fails.
    await session.commit()

    concepts = analysis.get("concepts") or []
    rels = analysis.get("concept_relationships") or []
    logger.info(
        "[events] doc %s analyzed: %d concepts, %d relationships",
        doc.id,
        len(concepts),
        len(rels),
    )

    await bus.publish(DocumentAnalyzed(document_id=doc.id, analysis=analysis))


@bus.on(DocumentAnalyzed)
async def merge_graph_and_retitle(event: DocumentAnalyzed, session: AsyncSession) -> None:
    """Merge concept relationships into the global graph + auto-name generic
    lectures from the analysis topic. Fast, DB-only — runs inline."""
    await merge_concept_graph(session, event.document_id, event.analysis)

    topic_title = (event.analysis.get("topic") or "").strip()
    if not topic_title:
        return

    stmt = select(LectureSession).where(
        (LectureSession.audio_doc_id == event.document_id)
        | (LectureSession.slides_doc_id == event.document_id)
    )
    res = await session.execute(stmt)
    for lecture in res.scalars().all():
        if (
            not lecture.title
            or lecture.title.startswith("Lecture ")
            or lecture.title.lower().startswith("untitled")
        ):
            logger.info(
                "[events] auto-naming lecture %s from '%s' to '%s'",
                lecture.id,
                lecture.title,
                topic_title,
            )
            lecture.title = topic_title


@bus.on(DocumentAnalyzed, background=True)
async def auto_generate_flashcards(
    event: DocumentAnalyzed, session: AsyncSession
) -> None:
    """Auto-generate a flashcard deck after analysis — the student never needs
    to click "Generate." Content is ready when they open the app.

    Flag-gated (auto_generate_flashcards) and deduped per document via the
    deck's origin="auto" tag.
    """
    if not settings.auto_generate_flashcards:
        return

    # Check for an existing auto-generated deck (dedup).
    existing = await session.execute(
        select(ContentItem).where(
            ContentItem.document_id == event.document_id,
            ContentItem.type == "flashcards",
        )
    )
    for item in existing.scalars().all():
        if isinstance(item.content, dict) and item.content.get("origin") == "auto":
            logger.info(
                "[events] doc %s already has auto-flashcards, skipping",
                event.document_id,
            )
            return

    doc = await session.get(Document, event.document_id)
    if doc is None:
        return

    from ...agent.graph import run_generation

    logger.info("[events] auto-generating flashcards for doc %s", doc.id)
    final_state = await run_generation(
        document_id=doc.id,
        document_text=doc.text,
        task_type="flashcards",
        session=session,
    )

    if final_state.get("error"):
        logger.warning(
            "[events] auto-gen failed for doc %s: %s",
            doc.id,
            final_state["error"],
        )
        return

    item = final_state.get("content_item")
    if not item:
        return

    # Tag the deck as auto-generated (the session composer distinguishes
    # auto decks this way).
    saved = await session.get(ContentItem, item["id"])
    if saved is not None:
        saved.content = {**saved.content, "origin": "auto"}
    logger.info(
        "[events] auto-generated flashcards for doc %s (content_id=%s)",
        doc.id,
        item["id"],
    )


async def _maybe_rename_document(session: AsyncSession, doc: Document) -> None:
    """Rename a document whose filename looks like machine-generated noise.

    The new name comes from the LLM (based on content); the original file
    extension is preserved.
    """
    if not tools.filename_needs_rename(doc.filename):
        return

    new_stem = await tools.suggest_filename(doc.filename, doc.text)
    if not new_stem:
        logger.info(
            "[events] keeping filename '%s' for doc %s (LLM says it's fine)",
            doc.filename,
            doc.id,
        )
        return

    # Preserve the original extension (webm, pdf, pptx, …).
    suffix = ""
    if "." in doc.filename:
        suffix = "." + doc.filename.rsplit(".", 1)[1]
    old_name = doc.filename
    doc.filename = f"{new_stem}{suffix}"
    logger.info(
        "[events] renamed doc %s: '%s' -> '%s'", doc.id, old_name, doc.filename
    )
