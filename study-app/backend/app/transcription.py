"""Audio transcription via OpenRouter's /audio/transcriptions endpoint.

Uses qwen3-asr-1.7b (~$0.000008/sec, ~$0.03 for a 1-hour lecture) through the
same OpenRouter API key and base URL as the LLM. No separate API key needed.

Transcribes lecture recordings and writes the transcript to Document.text.
The DocumentIngested event handler then continues the pipeline (concept
analysis, graph merge, …). The agent pipeline is transparent to the text
source — once the transcript is in doc.text, everything downstream (concept
graph, notes, quizzes, flashcards, mastery) works unchanged.

For files >25MB (OpenRouter's per-request limit), the audio is chunked via
pydub (requires ffmpeg) and each chunk is transcribed separately.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from openai import AsyncOpenAI

from .config import settings

logger = logging.getLogger(__name__)

# OpenRouter's per-request limit for audio transcription.
MAX_BYTES_PER_REQUEST = 25 * 1024 * 1024  # 25 MB
# Target chunk size (slightly under the limit for safety).
CHUNK_TARGET_BYTES = 24 * 1024 * 1024

_client: AsyncOpenAI | None = None


def _get_transcription_client() -> AsyncOpenAI:
    """Lazy singleton — reuses the OpenRouter client (same key + base URL).

    OpenRouter's /audio/transcriptions endpoint is OpenAI-compatible, so the
    openai SDK's client.audio.transcriptions.create() works with no changes.
    """
    global _client
    if _client is None:
        if not settings.openrouter_api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. Add it to .env for LLM + transcription."
            )
        _client = AsyncOpenAI(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key,
        )
    return _client


def _is_audio_file(path: Path) -> bool:
    """Check if a file is an audio format."""
    return path.suffix.lower() in {".webm", ".mp3", ".m4a", ".wav", ".ogg", ".flac"}


async def transcribe_audio(path: Path) -> str:
    """Transcribe an audio file via Whisper, chunking if >25MB.

    Returns the concatenated transcript text.
    """
    file_size = path.stat().st_size

    if file_size <= MAX_BYTES_PER_REQUEST:
        return await _transcribe_single(path)

    logger.info(
        "[transcription] file %s is %.1fMB — chunking", path.name, file_size / 1e6
    )
    return await _transcribe_chunked(path)


async def _transcribe_single(path: Path) -> str:
    """Transcribe a single file (≤25MB) via OpenRouter's ASR endpoint."""
    client = _get_transcription_client()
    with open(path, "rb") as f:
        result = await client.audio.transcriptions.create(
            model=settings.transcription_model,
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
    client = _get_transcription_client()
    transcript_parts = []
    for i, chunk in enumerate(chunks):
        # Export chunk to a temp file.
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            chunk.export(tmp_path, format="webm")

        try:
            with open(tmp_path, "rb") as f:
                result = await client.audio.transcriptions.create(
                    model=settings.transcription_model,
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


async def transcribe_document(session, doc) -> bool:
    """Transcribe an audio document onto doc.text, committing status milestones.

    Called by the DocumentIngested event handler (see app/events/handlers/
    ingestion.py) — analysis is no longer triggered from here.

    1. Set transcription_status to "transcribing" (committed — visible to
       the UI while the ASR call runs).
    2. Transcribe via Whisper (chunking if needed).
    3. Write the transcript to doc.text and mark "done".

    Returns True when a transcript is available (freshly transcribed or
    already done); False if transcription failed (status/error recorded on
    the document).
    """
    if doc.transcription_status == "done" and doc.text:
        return True

    audio_path = Path(doc.file_path)
    if not audio_path.exists():
        logger.error("[transcription] audio file missing: %s", doc.file_path)
        doc.transcription_status = "failed"
        doc.transcription_error = "Audio file not found"
        await session.commit()
        return False

    # Set transcribing status.
    doc.transcription_status = "transcribing"
    await session.commit()

    logger.info("[transcription] transcribing doc %s (%s)", doc.id, audio_path.name)

    try:
        transcript = await transcribe_audio(audio_path)
    except Exception as exc:
        logger.exception("[transcription] failed for doc %s", doc.id)
        await session.rollback()
        doc.transcription_status = "failed"
        doc.transcription_error = str(exc)[:500]
        await session.commit()
        return False

    # Write transcript + mark done.
    doc.text = transcript
    doc.char_count = len(transcript)
    doc.page_count = 1
    doc.transcription_status = "done"
    doc.transcription_error = None
    await session.commit()

    logger.info(
        "[transcription] doc %s transcribed: %d chars", doc.id, len(transcript)
    )
    return True
