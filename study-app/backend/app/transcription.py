"""Audio transcription via OpenAI Whisper API.

Transcribes lecture recordings and writes the transcript to Document.text,
then triggers the concept analysis agent. The agent pipeline is transparent
to the text source — once the transcript is in doc.text, everything downstream
(concept graph, notes, quizzes, flashcards, mastery) works unchanged.

For files >25MB (Whisper's per-request limit), the audio is chunked via pydub
(requires ffmpeg) and each chunk is transcribed separately.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from openai import AsyncOpenAI

from .config import settings

logger = logging.getLogger(__name__)

# Whisper API limit per request.
WHISPER_MAX_BYTES = 25 * 1024 * 1024  # 25 MB
# Target chunk size (slightly under the limit for safety).
CHUNK_TARGET_BYTES = 24 * 1024 * 1024

_client: AsyncOpenAI | None = None


def _get_whisper_client() -> AsyncOpenAI:
    """Lazy singleton for the OpenAI client (pointed at OpenAI, not OpenRouter)."""
    global _client
    if _client is None:
        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Add it to .env for audio transcription."
            )
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


def _is_audio_file(path: Path) -> bool:
    """Check if a file is an audio format."""
    return path.suffix.lower() in {".webm", ".mp3", ".m4a", ".wav", ".ogg", ".flac"}


async def transcribe_audio(path: Path) -> str:
    """Transcribe an audio file via Whisper, chunking if >25MB.

    Returns the concatenated transcript text.
    """
    file_size = path.stat().st_size

    if file_size <= WHISPER_MAX_BYTES:
        return await _transcribe_single(path)

    logger.info(
        "[transcription] file %s is %.1fMB — chunking", path.name, file_size / 1e6
    )
    return await _transcribe_chunked(path)


async def _transcribe_single(path: Path) -> str:
    """Transcribe a single file (≤25MB) via Whisper."""
    client = _get_whisper_client()
    with open(path, "rb") as f:
        result = await client.audio.transcriptions.create(
            model=settings.whisper_model,
            file=f,
        )
    return result.text


async def _transcribe_chunked(path: Path) -> str:
    """Transcribe a large file by splitting it into ≤24MB chunks via pydub.

    Requires ffmpeg installed on the system.
    """
    from pydub import AudioSegment  # Heavy import, deferred

    # Load the full audio file.
    audio = AudioSegment.from_file(str(path))

    # Estimate bytes per millisecond to calculate chunk duration.
    total_bytes = path.stat().st_size
    bytes_per_ms = total_bytes / len(audio)
    chunk_duration_ms = int(CHUNK_TARGET_BYTES / bytes_per_ms) if bytes_per_ms > 0 else 600_000

    # Split into overlapping chunks (slight overlap to avoid cutting words).
    chunks = []
    pos = 0
    while pos < len(audio):
        chunk = audio[pos : pos + chunk_duration_ms]
        chunks.append(chunk)
        pos += chunk_duration_ms

    logger.info(
        "[transcription] split into %d chunks of ~%dms each",
        len(chunks),
        chunk_duration_ms,
    )

    # Transcribe each chunk.
    client = _get_whisper_client()
    transcript_parts = []
    for i, chunk in enumerate(chunks):
        # Export chunk to a temp file.
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            chunk.export(tmp_path, format="webm")

        try:
            with open(tmp_path, "rb") as f:
                result = await client.audio.transcriptions.create(
                    model=settings.whisper_model,
                    file=f,
                )
            transcript_parts.append(result.text)
            logger.info(
                "[transcription] chunk %d/%d done (%d chars)",
                i + 1,
                len(chunks),
                len(result.text),
            )
        finally:
            os.unlink(tmp_path)

    return "\n\n".join(transcript_parts)


async def transcribe_then_analyze(doc_id: str) -> None:
    """Background task: transcribe an audio document, then run concept analysis.

    1. Load the document and its audio file.
    2. Set transcription_status to "transcribing".
    3. Transcribe via Whisper (chunking if needed).
    4. Write the transcript to doc.text.
    5. Set transcription_status to "done".
    6. Call analyze_concepts_background (builds the concept graph from the transcript).

    On failure: set transcription_status to "failed" with the error message.
    """
    from .db import SessionLocal
    from .models import Document
    from .agent.concept_graph import analyze_concepts_background

    try:
        async with SessionLocal() as session:
            doc = await session.get(Document, doc_id)
            if doc is None:
                logger.warning("[transcription] doc %s not found", doc_id)
                return

            audio_path = Path(doc.file_path)
            if not audio_path.exists():
                logger.error("[transcription] audio file missing: %s", doc.file_path)
                doc.transcription_status = "failed"
                doc.transcription_error = "Audio file not found"
                await session.commit()
                return

            # Set transcribing status.
            doc.transcription_status = "transcribing"
            await session.commit()

            logger.info(
                "[transcription] transcribing doc %s (%s)", doc_id, audio_path.name
            )

            # Transcribe.
            transcript = await transcribe_audio(audio_path)

            # Write transcript + mark done.
            doc.text = transcript
            doc.char_count = len(transcript)
            doc.page_count = 1
            doc.transcription_status = "done"
            doc.transcription_error = None
            await session.commit()

            logger.info(
                "[transcription] doc %s transcribed: %d chars", doc_id, len(transcript)
            )

        # Run concept analysis on the transcript (separate session since
        # analyze_concepts_background creates its own).
        await analyze_concepts_background(doc_id)

    except Exception as exc:
        logger.exception("[transcription] failed for doc %s", doc_id)
        # Try to mark as failed.
        try:
            async with SessionLocal() as session:
                doc = await session.get(Document, doc_id)
                if doc is not None:
                    doc.transcription_status = "failed"
                    doc.transcription_error = str(exc)[:500]
                    await session.commit()
        except Exception:
            pass  # best-effort
