"""Stage 1 — chunk GovReport reports via DeepSeek.

For each of ``--subset N`` reports (proportional across splits), call DeepSeek
with the chunking prompt and validate against :class:`ChunkingResponse`.

Long reports (> ``--max-report-chars``) are pre-split on paragraph boundaries
and each section is chunked independently, with the resulting chunk lists
concatenated in document order.

Output: ``intermediate/chunks.jsonl`` — one JSON object per chunk.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import httpx
from tqdm.asyncio import tqdm as atqdm

# Allow `python -m scripts.01_chunk_reports` and `python 01_chunk_reports.py`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.cache import DiskCache
    from scripts.common import (
        CHUNKS_JSONL,
        CACHE_DIR,
        INTERMEDIATE_DIR,
        add_common_args,
        ensure_dirs,
        resolve_api_key,
        setup_logging,
    )
    from scripts.dataset_io import (
        disambiguate_titles,
        load_govreport_subset,
        read_jsonl_id_set,
        split_paragraphs,
    )
    from scripts.deepseek_client import LLMJsonError, call_json
    from scripts.prompts import (
        CHUNK_SECTION_USER_TEMPLATE,
        CHUNK_USER_TEMPLATE,
        SYSTEM,
    )
    from scripts.schemas import ChunkingResponse
else:
    from ..scripts.cache import DiskCache
    from ..scripts.common import (
        CHUNKS_JSONL,
        CACHE_DIR,
        INTERMEDIATE_DIR,
        add_common_args,
        ensure_dirs,
        resolve_api_key,
        setup_logging,
    )
    from ..scripts.dataset_io import (
        disambiguate_titles,
        load_govreport_subset,
        read_jsonl_id_set,
        split_paragraphs,
    )
    from ..scripts.deepseek_client import LLMJsonError, call_json
    from ..scripts.prompts import (
        CHUNK_SECTION_USER_TEMPLATE,
        CHUNK_USER_TEMPLATE,
        SYSTEM,
    )
    from ..scripts.schemas import ChunkingResponse

logger = logging.getLogger(__name__)

# ~4 chars/token -> 24_000 chars ≈ 6_000 tokens input. Leaves room for the
# verbatim chunks in the output.
DEFAULT_MAX_REPORT_CHARS = 24_000


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Stage 1 — LLM-chunk GovReport reports")
    add_common_args(p)
    p.add_argument(
        "--max-report-chars", type=int, default=DEFAULT_MAX_REPORT_CHARS,
        help=f"Reports larger than this are pre-split on paragraphs (default: {DEFAULT_MAX_REPORT_CHARS})",
    )
    return p


def estimate_input_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token). Used only to size max_tokens."""
    return max(1, len(text)) // 4


def section_long_report(
    text: str, max_chars: int
) -> list[tuple[str, int, int]]:
    """Group paragraphs of a long report into sections ≤ ``max_chars`` each.

    Returns a list of ``(section_text, section_index_1based, section_total)``.
    The total is filled in by the caller (we don't know it yet here).
    """
    paragraphs = split_paragraphs(text)
    sections: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for para in paragraphs:
        plen = len(para) + 2  # account for "\n\n" joiner
        if buf and buf_len + plen > max_chars:
            sections.append("\n\n".join(buf))
            buf = [para]
            buf_len = plen
        else:
            buf.append(para)
            buf_len += plen
    if buf:
        sections.append("\n\n".join(buf))

    return [(s, i + 1, len(sections)) for i, s in enumerate(sections)]


async def chunk_one_report(
    client: httpx.AsyncClient,
    *,
    report_id: str,
    text: str,
    model: str,
    api_key: str,
    sem: asyncio.Semaphore,
    cache: DiskCache,
    use_cache: bool,
    max_report_chars: int,
) -> list[dict]:
    """Chunk a single report, returning a list of {title, text} dicts (LLM order)."""
    if len(text) <= max_report_chars:
        sections = [(text, 1, 1)]
    else:
        sections = section_long_report(text, max_report_chars)
        logger.info(
            "%s: long report (%d chars) split into %d sections",
            report_id, len(text), len(sections),
        )

    all_chunks: list[dict] = []
    for section_text, idx, total in sections:
        user = (
            CHUNK_USER_TEMPLATE.format(report_id=report_id, report_body=section_text)
            if total == 1
            else CHUNK_SECTION_USER_TEMPLATE.format(
                report_id=report_id,
                report_body=section_text,
                section_index=idx,
                section_total=total,
            )
        )
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ]
        est_tokens = estimate_input_tokens(section_text)
        max_tokens = max(2000, est_tokens + 1000)

        try:
            resp = await call_json(
                client,
                model=model,
                messages=messages,
                temperature=0.0,
                max_tokens=max_tokens,
                response_model=ChunkingResponse,
                sem=sem,
                cache=cache,
                api_key=api_key,
                use_cache=use_cache,
            )
        except LLMJsonError as e:
            logger.error("%s section %d/%d: JSON failure — skipping section: %s",
                         report_id, idx, total, e)
            continue
        except Exception as e:
            logger.error("%s section %d/%d: error — skipping section: %s",
                         report_id, idx, total, e)
            continue

        for c in resp.chunks:
            all_chunks.append({"title": c.title, "text": c.text})

    return all_chunks


async def run(args: argparse.Namespace) -> int:
    ensure_dirs()
    api_key = resolve_api_key(args.api_key)
    cache = DiskCache(CACHE_DIR)
    sem = asyncio.Semaphore(args.concurrency)

    # Resume support: if chunks.jsonl already exists and --restart was NOT
    # passed, collect the set of report_ids we've already chunked and skip
    # them. The cache (cache/) handles per-section recovery; this skip handles
    # report-level recovery so a crashed run picks up where it left off.
    if args.restart and CHUNKS_JSONL.exists():
        logger.info("--restart: truncating %s", CHUNKS_JSONL)
        CHUNKS_JSONL.unlink()
    done_reports = read_jsonl_id_set(CHUNKS_JSONL, "report_id") if CHUNKS_JSONL.exists() else set()
    if done_reports:
        logger.info(
            "Resume: %d report(s) already in %s — will skip.",
            len(done_reports), CHUNKS_JSONL,
        )

    logger.info("Loading govreport (subset=%s, split=%s)...", args.subset, args.split)
    reports = load_govreport_subset(subset=args.subset, split=args.split)
    logger.info("Loaded %d reports", len(reports))

    todo = [r for r in reports if r["report_id"] not in done_reports]
    skipped = len(reports) - len(todo)
    logger.info(
        "To chunk: %d report(s) (%d skipped as already-done)",
        len(todo), skipped,
    )
    if not todo:
        logger.info("Nothing to do — all reports already chunked.")
        return 0

    # Open the output file in APPEND mode. Writes are guarded by an asyncio
    # lock so concurrent tasks can't interleave lines, and each row is flushed
    # immediately so a crash leaves a usable partial file.
    CHUNKS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    out_lock = asyncio.Lock()
    out_file = CHUNKS_JSONL.open("a", encoding="utf-8")
    total_written = 0

    timeout = __import__("httpx").Timeout(connect=10.0, read=120.0, write=10.0, pool=30.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, http2=True) as client:
            async def task_for(r: dict) -> None:
                nonlocal total_written
                chunks = await chunk_one_report(
                    client,
                    report_id=r["report_id"],
                    text=r["text"],
                    model=args.model,
                    api_key=api_key,
                    sem=sem,
                    cache=cache,
                    use_cache=not args.no_cache,
                    max_report_chars=args.max_report_chars,
                )
                # Disambiguate duplicate titles within this single report.
                disambiguate_titles(chunks)
                # Build rows with chunk_ids assigned in document order.
                rows = [
                    {
                        "report_id": r["report_id"],
                        "chunk_id": f"{r['report_id']}__c{i}",
                        "title": c["title"],
                        "text": c["text"],
                        "chunk_index": i,
                        "model": args.model,
                    }
                    for i, c in enumerate(chunks)
                ]
                # Atomic-ish write: hold the lock across the whole flush so
                # rows from one report stay contiguous on disk.
                async with out_lock:
                    for row in rows:
                        out_file.write(json.dumps(row, ensure_ascii=False))
                        out_file.write("\n")
                        out_file.flush()
                    total_written += len(rows)
                logger.info("%s: wrote %d chunk(s)", r["report_id"], len(rows))

            await atqdm.gather(
                *(task_for(r) for r in todo),
                desc="chunking",
                file=sys.stderr,
            )
    finally:
        out_file.flush()
        out_file.close()

    logger.info("Wrote %d new chunk(s) to %s", total_written, CHUNKS_JSONL)
    if CHUNKS_JSONL.exists():
        with CHUNKS_JSONL.open("r", encoding="utf-8") as f:
            total = sum(1 for line in f if line.strip())
        logger.info("Total chunks now on disk: %d", total)
    return total_written


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.verbose)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
