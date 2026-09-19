"""Local web UI — collaborative human/agent editing of blog drafts.

FastAPI app + a static single-page editor (no build step; EasyMDE from CDN).
The editor content is the single shared artifact: every agent action (full
draft, targeted fixes, chat-style revisions) takes the CURRENT markdown from
the client and returns updated markdown, so human edits are never lost.
Version history for AI edits lives client-side (revert button).

Run with `wa ui` (default http://127.0.0.1:8765).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import CONFIG_DIR, get_settings
from .graph import GRAPH
from .lint.banned_tokens import load_patterns
from .lint.report import run_all_linters
from .llm import chat
from .nodes.editor import editor_node
from .scoring.discourse import (
    AI_CONTROLS,
    analyze_discourse,
    authorship_distance,
    human_score,
    project_narrative_space,
    rarity_percentiles,
    shape_axes,
    shape_metrics,
    trigram_novelty,
)
from .scoring.semantic import axis_value, semantic_report
from .scoring.surprisal import score_blocks
from .segment import slugify, split_blocks

# Local tool: make all writing_agent INFO logs visible under uvicorn.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-22s %(levelname)s %(message)s",
)

logger = logging.getLogger(__name__)

app = FastAPI(title="writing-agent")

_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

NODE_LABELS = {
    "architect": "Designing the outline…",
    "stylist": "Drafting sections…",
    "audit": "Running the gates…",
    "editor": "Fixing flagged blocks…",
    "finalize": "Assembling the post…",
}

REVISE_SYSTEM = """You are the human author's co-editor. You and the author share one
markdown document. Apply their instruction with the smallest edits that satisfy it.

Discipline:
- Preserve the author's voice, opinions, and unresolved trade-offs. Do not sand
  the text into corporate polish.
- Preserve every technical claim, number, command, and code block unless the
  instruction targets them.
- Vary sentence length; prefer parentheticals over em-dashes; never open
  paragraphs with Furthermore/Additionally/Moreover.
- Banned words (never emit): {banned}
- Return ONLY the rewritten text — no commentary, no code fences."""


# ---- request models ----

class DraftRequest(BaseModel):
    topic: str
    research: str = ""
    persona: str = "default"


class MarkdownRequest(BaseModel):
    markdown: str


class SaveRequest(BaseModel):
    markdown: str
    source: str = "human"  # human | agent | fix | pipeline | revert


class FixRequest(BaseModel):
    markdown: str
    block_indices: list[int] | None = None  # None = all flagged


class ReviseRequest(BaseModel):
    markdown: str
    instruction: str
    block_index: int | None = None  # revise only this block
    selection: str | None = None  # revise only this selected passage


# ---- helpers ----

def _workspace() -> Path:
    ws = get_settings().workspace_dir
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _snapshot(slug: str, source: str) -> None:
    """Copy the draft's CURRENT content into .history before it changes."""
    current = _workspace() / f"{slugify(slug)}.md"
    if not current.exists() or not current.read_text().strip():
        return  # nothing worth snapshotting
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f")[:-3]
    history_dir = _workspace() / ".history" / slugify(slug)
    history_dir.mkdir(parents=True, exist_ok=True)
    safe_source = "".join(c if c.isalnum() else "-" for c in source)[:16]
    (history_dir / f"{ts}-{safe_source}.md").write_text(current.read_text())


def _history_dir(slug: str) -> Path:
    return _workspace() / ".history" / slugify(slug)


# ---- draft file CRUD (workspace markdown is the source of truth) ----

@app.get("/api/drafts")
def list_drafts():
    out = []
    for f in sorted(_workspace().glob("*.md")):
        if f.name.endswith(".scorecard.md"):
            continue
        out.append({"slug": f.stem, "mtime": f.stat().st_mtime})
    return out


@app.get("/api/drafts/{slug}")
def get_draft(slug: str):
    p = _workspace() / f"{slugify(slug)}.md"
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"no draft {slug!r}")
    return {"slug": p.stem, "markdown": p.read_text()}


@app.put("/api/drafts/{slug}")
def save_draft(slug: str, req: SaveRequest):
    slug = slugify(slug)
    _snapshot(slug, req.source)  # previous content → .history before overwrite
    p = _workspace() / f"{slug}.md"
    p.write_text(req.markdown)
    return {"ok": True, "slug": slug}


@app.get("/api/drafts/{slug}/versions")
def list_versions(slug: str):
    d = _history_dir(slug)
    if not d.exists():
        return {"versions": []}
    versions = []
    for f in sorted(d.glob("*.md"), reverse=True):  # name starts with UTC ts
        source = f.stem.split("-", 1)[1] if "-" in f.stem else "unknown"
        versions.append(
            {"file": f.name, "mtime": f.stat().st_mtime, "source": source}
        )
    return {"versions": versions[:100]}


@app.get("/api/drafts/{slug}/versions/{file}")
def get_version(slug: str, file: str):
    p = _history_dir(slug) / Path(file).name  # name-only: no traversal
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"no version {file!r}")
    return {"file": p.name, "markdown": p.read_text()}


class RevertRequest(BaseModel):
    file: str


@app.post("/api/drafts/{slug}/revert")
def revert_draft(slug: str, req: RevertRequest):
    slug = slugify(slug)
    version = _history_dir(slug) / Path(req.file).name
    if not version.exists():
        raise HTTPException(status_code=404, detail=f"no version {req.file!r}")
    _snapshot(slug, "revert")
    (_workspace() / f"{slug}.md").write_text(version.read_text())
    return {"ok": True, "slug": slug, "restored": version.name}


# ---- full-pipeline jobs (polled; stage labels stream from LangGraph) ----

_jobs: dict[str, dict[str, Any]] = {}
_tasks: list[asyncio.Task] = []  # keep refs so jobs aren't GC'd


def start_draft_job(initial: dict[str, Any]) -> str:
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {
        "id": job_id,
        # architect is statically the first node — label it as such, not
        # "starting…", so the first (slowest-to-yield) LLM call is visible.
        "stage": NODE_LABELS["architect"],
        "done": False,
        "error": None,
        "result": None,
        "started": time.time(),
        "trace": [],
        "subscribers": [],  # SSE queues
    }
    _tasks.append(asyncio.get_running_loop().create_task(_run_job(job_id, initial)))
    return job_id


def _emit(job_id: str, kind: str, payload: dict[str, Any] | None = None) -> None:
    """Fan an event out to every SSE subscriber of this job."""
    job = _jobs.get(job_id)
    if not job:
        return
    event = {"kind": kind, "stage": job["stage"], "elapsed": round(time.time() - job["started"], 1)}
    if payload:
        event.update(payload)
    for q in list(job["subscribers"]):
        q.put_nowait(event)


async def _run_job(job_id: str, initial: dict[str, Any]) -> None:
    job = _jobs[job_id]

    def trace(event: str) -> None:
        elapsed = round(time.time() - job["started"], 1)
        job["trace"].append({"t": elapsed, "event": event})
        logger.info("job %s [%6.1fs] %s", job_id, elapsed, event)
        _emit(job_id, "trace", {"event": event})

    settings = get_settings()
    trace(
        f"pipeline start: topic={initial.get('topic')!r} "
        f"research_chars={len(initial.get('raw_research', ''))} "
        f"api_key_loaded={bool(settings.openrouter_api_key)} "
        f"draft_model={settings.models.draft} "
        f"scorer_model={settings.models.scorer}"
    )
    state: dict[str, Any] = dict(initial)
    try:
        async for update in GRAPH.astream(initial, stream_mode="updates"):
            for node, delta in update.items():
                state.update(delta)
                job["stage"] = NODE_LABELS.get(node, node)
                trace(
                    f"node done: {node} "
                    f"(flagged={delta.get('flagged_blocks')} "
                    f"revision_count={delta.get('revision_count')})"
                )
        job["result"] = _persist_final(state)
        trace(f"finished → slug={job['result']['slug']}")
    except Exception as exc:
        job["error"] = str(exc)
        logger.exception("job %s FAILED", job_id)
        trace(f"FAILED: {type(exc).__name__}: {exc}")
    finally:
        job["done"] = True
        _emit(job_id, "done", {"error": job["error"], "result": job["result"]})


def _persist_final(state: dict[str, Any]) -> dict[str, Any]:
    post = state.get("final_post")
    if not post:
        raise RuntimeError("pipeline produced no final_post")
    slug = slugify(state["topic"])
    _snapshot(slug, "pipeline")  # previous draft (if any) → .history
    (_workspace() / f"{slug}.md").write_text(post)
    (_workspace() / f"{slug}.scorecard.md").write_text(state.get("scorecard", ""))
    return {
        "slug": slug,
        "scorecard": state.get("scorecard", ""),
        "flagged_blocks": state.get("flagged_blocks", []),
    }


@app.post("/api/drafts")
async def start_draft(req: DraftRequest):
    if not req.topic.strip():
        raise HTTPException(status_code=422, detail="topic is required")
    persona_path = CONFIG_DIR / "personas" / f"{req.persona}.md"
    persona = persona_path.read_text() if persona_path.exists() else ""
    job_id = start_draft_job(
        {
            "topic": req.topic,
            "persona": persona,
            "raw_research": req.research,
            "style_reference_paths": [],
        }
    )
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"no job {job_id!r}")
    return {**job, "elapsed": round(time.time() - job["started"], 1)}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str):
    """SSE stream of job trace/stage events. Catches a late subscriber up
    with the current state first, then streams until done."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"no job {job_id!r}")
    queue: asyncio.Queue = asyncio.Queue()

    async def stream():
        job["subscribers"].append(queue)
        try:
            # catch-up: current snapshot + everything already traced
            yield _sse({"kind": "stage", "stage": job["stage"],
                        "elapsed": round(time.time() - job["started"], 1)})
            for entry in job["trace"]:
                yield _sse({"kind": "trace", "stage": job["stage"],
                            "elapsed": entry["t"], "event": entry["event"]})
            if job["done"]:
                yield _sse({"kind": "done", "stage": job["stage"],
                            "elapsed": round(time.time() - job["started"], 1),
                            "error": job["error"], "result": job["result"]})
                return
            while True:
                event = await queue.get()
                yield _sse(event)
                if event.get("kind") == "done":
                    return
        finally:
            if queue in job["subscribers"]:
                job["subscribers"].remove(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


# ---- gates on the CURRENT editor content ----

@app.post("/api/lint")
def lint(req: MarkdownRequest):
    report = run_all_linters(split_blocks(req.markdown), get_settings().thresholds)
    return {"report": report.model_dump(), "scorecard": report.scorecard()}


@app.post("/api/score")
async def score(req: MarkdownRequest):
    t = get_settings().thresholds
    return await score_blocks(
        split_blocks(req.markdown),
        overlap_max=t.continuation_overlap_max,
        bits_max=t.continuation_bits_max,
        convergence_max=t.continuation_convergence_max,
    )


@app.post("/api/shape")
async def shape(req: MarkdownRequest):
    """StoryScope-style discourse-shape audit of the current editor text."""
    report = await analyze_discourse(split_blocks(req.markdown), get_settings().thresholds)
    report["semantic"] = await semantic_report(
        split_blocks(req.markdown), step_min=get_settings().thresholds.semantic_step_min
    )
    return report


@app.post("/api/shape/compare")
async def shape_compare(req: MarkdownRequest):
    """Graph data: radar axes + PCA narrative space for the editor text,
    saved drafts, human references, and an AI-shaped control sample."""
    current_blocks = split_blocks(req.markdown)
    docs: list[tuple[str, str, list[str]]] = [("current", "current", current_blocks)]

    for folder, group in (
        (get_settings().workspace_dir, "draft"),
        (CONFIG_DIR.parent / "references", "human"),
    ):
        if not folder.exists():
            continue
        for f in sorted(folder.glob("*.md")):
            if f.name.endswith(".scorecard.md") or f.name == "README.md":
                continue
            blocks = split_blocks(f.read_text())
            if shape_metrics(blocks)["words"] >= 50:
                docs.append((f.stem, group, blocks))
    for i, text in enumerate(AI_CONTROLS, start=1):
        docs.append((f"AI control {i}", "ai", split_blocks(text)))

    axes_list = [shape_axes(shape_metrics(blocks)) for _, _, blocks in docs]
    # Genie-style novelty as the 9th axis, leave-one-out: each document is
    # scored against the pooled 3-grams of every OTHER document.
    for i, (_, _, blocks) in enumerate(docs):
        corpus = [b for j, (_, _, other) in enumerate(docs) if j != i for b in other]
        axes_list[i]["Novelty"] = trigram_novelty(blocks, corpus)
    # Semantic-jump axis (10th): embedding glide per document. Added to ALL
    # docs or none — PCA vectors must stay uniform.
    semantic_axes: list[float | None] = []
    for _, _, blocks in docs:
        try:
            report = await semantic_report(
                blocks, step_min=get_settings().thresholds.semantic_step_min
            )
            semantic_axes.append(axis_value(report))
        except Exception:
            semantic_axes.append(None)
    if all(a is not None for a in semantic_axes):
        for axes, value in zip(axes_list, semantic_axes):
            axes["Semantic jumps"] = value
    coords = project_narrative_space(axes_list)
    rarities = rarity_percentiles([human_score(a) for a in axes_list])
    payload_docs = [
        {
            "label": label,
            "group": group,
            "x": coords[i][0],
            "y": coords[i][1],
            "axes": axes_list[i],
            "human_score": human_score(axes_list[i]),
            "rarity": rarities[i],
        }
        for i, (label, group, _) in enumerate(docs)
    ]

    current_report = await analyze_discourse(
        current_blocks, get_settings().thresholds
    )
    authorship = authorship_distance(
        axes_list[0],
        [axes_list[i] for i, (_, g, _) in enumerate(docs) if g == "human"],
        [axes_list[i] for i, (_, g, _) in enumerate(docs) if g == "ai"],
    )
    return {
        "axes_names": list(axes_list[0].keys()) if axes_list else [],
        "docs": payload_docs,
        "judge": current_report.get("judge"),
        "authorship": authorship,
    }


@app.post("/api/fix")
async def fix(req: FixRequest):
    """Deterministic lint → surgical rewrite of flagged blocks only."""
    blocks = split_blocks(req.markdown)
    report = run_all_linters(blocks, get_settings().thresholds)
    flagged = (
        req.block_indices
        if req.block_indices is not None
        else report.flagged_blocks
    )
    flagged = sorted({i for i in flagged if 0 <= i < len(blocks)})
    if not flagged:
        return {
            "changed": False,
            "markdown": req.markdown,
            "reasons": {},
        }
    failures = [f.model_dump() for f in report.failures if f.block_index in flagged]
    state = {
        "draft_blocks": blocks,
        "flagged_blocks": flagged,
        "revision_count": 1,
        "lint_report": {"failures": failures, "metrics": report.metrics},
        "surprisal_report": {"blocks": []},
    }
    out = await editor_node(state)
    return {
        "changed": True,
        "markdown": "\n\n".join(out["draft_blocks"]),
        "reasons": {str(f["block_index"]): f["reasons"] for f in failures},
    }


@app.post("/api/revise")
async def revise(req: ReviseRequest):
    """Chat-style co-editing: apply an instruction to the shared document.

    Scope priority: explicit block_index > selected passage > whole document.
    """
    if not req.instruction.strip():
        raise HTTPException(status_code=422, detail="instruction is required")
    system = REVISE_SYSTEM.format(banned=", ".join(load_patterns()[:12]))
    blocks = split_blocks(req.markdown)

    if req.block_index is not None and 0 <= req.block_index < len(blocks):
        prompt = (
            f"FULL DOCUMENT (context only, do not rewrite):\n{req.markdown}\n\n"
            f"EDIT ONLY THIS BLOCK:\n{blocks[req.block_index]}\n\n"
            f"INSTRUCTION:\n{req.instruction}"
        )
        revised = await chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
        blocks[req.block_index] = revised
        return {
            "markdown": "\n\n".join(blocks),
            "scope": "block",
            "block_index": req.block_index,
        }

    if req.selection and req.selection in req.markdown:
        prompt = (
            f"FULL DOCUMENT (context only):\n{req.markdown}\n\n"
            f"REWRITE ONLY THIS PASSAGE:\n{req.selection}\n\n"
            f"INSTRUCTION:\n{req.instruction}"
        )
        revised = await chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
        return {
            "markdown": req.markdown.replace(req.selection, revised, 1),
            "scope": "selection",
        }

    prompt = (
        f"DOCUMENT:\n{req.markdown}\n\nINSTRUCTION:\n{req.instruction}\n\n"
        "Return the full revised document."
    )
    revised = await chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
    )
    return {"markdown": revised, "scope": "document"}


# ---- SPA frontend (frontend/dist, built with `npm run build`) ----

@app.get("/", include_in_schema=False)
def index():
    index_html = _FRONTEND_DIST / "index.html"
    if index_html.exists():
        return FileResponse(index_html)
    return PlainTextResponse(
        "writing-agent frontend is not built. Run:\n"
        "  cd writing-agent/frontend && npm install && npm run build\n"
        "then restart `wa ui`. For hot-reload development: `npm run dev` "
        "in one shell (proxies /api to :8765) and `uv run wa ui` in another.",
        status_code=503,
    )


@app.get("/{path:path}", include_in_schema=False)
def spa_fallback(path: str):
    """Serve built assets; anything else falls back to the SPA shell."""
    if path.startswith("api/"):
        raise HTTPException(status_code=404, detail=f"no route /{path}")
    candidate = _FRONTEND_DIST / path
    if path and candidate.is_file():
        return FileResponse(candidate)
    return index()


_assets_dir = _FRONTEND_DIST / "assets"
if _assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")
