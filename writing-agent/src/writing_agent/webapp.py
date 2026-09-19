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
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
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

_STATIC = Path(__file__).resolve().parent / "static"

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
    p = _workspace() / f"{slugify(slug)}.md"
    p.write_text(req.markdown)
    return {"ok": True, "slug": p.stem}


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
    }
    _tasks.append(asyncio.get_running_loop().create_task(_run_job(job_id, initial)))
    return job_id


async def _run_job(job_id: str, initial: dict[str, Any]) -> None:
    job = _jobs[job_id]

    def trace(event: str) -> None:
        elapsed = round(time.time() - job["started"], 1)
        job["trace"].append({"t": elapsed, "event": event})
        logger.info("job %s [%6.1fs] %s", job_id, elapsed, event)

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


def _persist_final(state: dict[str, Any]) -> dict[str, Any]:
    post = state.get("final_post")
    if not post:
        raise RuntimeError("pipeline produced no final_post")
    slug = slugify(state["topic"])
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


# ---- static frontend ----

@app.get("/")
def index():
    return FileResponse(_STATIC / "index.html")


app.mount("/static", StaticFiles(directory=_STATIC), name="static")
