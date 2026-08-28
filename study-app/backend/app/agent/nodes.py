"""LangGraph nodes — each step in the agent pipeline.

Flow: analyze_document → retrieve_memory → plan → generate → validate → finalize

Memory is retrieved before planning so the planner can weight toward weak
topics and due concepts.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import user_ref_id
from ..models import ContentItem
from . import tools
from . import memory as memory_store
from .state import AgentState

logger = logging.getLogger(__name__)


def _trace(state: AgentState, msg: str) -> None:
    state.setdefault("messages", []).append(msg)
    logger.info("[agent] %s", msg)


# --- Node 1: analyze_document ---


async def analyze_document(state: AgentState) -> dict[str, Any]:
    """Extract document structure. Cached in per-doc memory."""
    session: AsyncSession = state["session"]
    doc_id = state["document_id"]

    cached = await memory_store.read_memory(session, "doc", doc_id, "analysis")
    if cached:
        _trace(state, f"analysis: cache hit for doc {doc_id}")
        return {"analysis": cached}

    analysis = await tools.analyze_document(state["document_text"])
    await memory_store.write_memory(session, "doc", doc_id, "analysis", analysis)
    _trace(state, f"analysis: extracted {len(analysis.get('concepts', []))} concepts")
    return {"analysis": analysis}


# --- Node 2: plan ---


async def plan(state: AgentState) -> dict[str, Any]:
    """Decide how to generate the requested content type.

    Runs after retrieve_memory, so the memory dict (weak topics, concept
    mastery, learner profile) is populated and the plan can be personalized.
    """
    plan_result = await tools.plan_task(
        task_type=state["task_type"],
        analysis=state.get("analysis", {}),
        memory=state.get("memory", {}),
        instructions=state.get("instructions"),
    )
    _trace(
        state,
        f"plan: {state['task_type']} -> {json_summary(plan_result)}",
    )
    return {"plan": plan_result}


# --- Node 3: retrieve_memory (runs second in the graph; defined here) ---


async def retrieve_memory(state: AgentState) -> dict[str, Any]:
    """Pull together all relevant memory for the generation step."""
    session: AsyncSession = state["session"]
    doc_id = state["document_id"]

    doc_memory = await memory_store.read_memory_scope(session, "doc", doc_id)
    user_memory = await memory_store.read_memory_scope(session, "user", user_ref_id())

    # Compose a focused memory dict the generation tools know how to read.
    memory: dict[str, Any] = {}
    weak_raw = user_memory.get("weak_topics") or []
    # weak_topics is stored as [{"topic": str, "missed_count": int, ...}] dicts;
    # the generation tools only need the topic names as strings.
    weak_names = [
        w["topic"] if isinstance(w, dict) else str(w) for w in weak_raw
    ]
    if weak_names:
        memory["weak_topics"] = weak_names
    if "notes_style" in user_memory:
        memory["notes_style"] = user_memory["notes_style"]
    prior = doc_memory.get("prior_generations") or []
    if prior:
        memory["prior_generations"] = prior
    attempts = doc_memory.get("quiz_attempts")
    if attempts:
        memory["quiz_attempts"] = attempts

    # Per-concept mastery: the richest signal. Intersect the doc's concepts
    # with the learner's mastery history so the plan/generate steps can weight
    # toward weak areas and skip mastered ones.
    analysis = doc_memory.get("analysis") or {}
    doc_concepts = [str(c) for c in (analysis.get("concepts") or [])]
    if doc_concepts:
        mastery = await memory_store.get_mastery_for_concepts(
            session, doc_concepts
        )
        if mastery:
            # Annotate each concept with FSRS due status so the plan/generate
            # steps can prioritize concepts due for spaced-repetition review.
            from . import fsrs_scheduler

            for entry in mastery:
                fsrs = entry.get("fsrs")
                entry["due"] = fsrs_scheduler.is_due(fsrs)
                entry["due_in_days"] = fsrs_scheduler.due_in_days(fsrs)
            memory["concept_mastery"] = mastery

    # Learner profile: who the learner is (level, preferred difficulty/formats,
    # study goal). Grows automatically from interactions.
    profile = await memory_store.get_learner_profile(session)
    memory["learner_profile"] = profile

    # Behavioral understanding (in-app actions as memory): the LLM
    # reflection's narrative + the deterministic aggregates from
    # agent/behavior.py, so generation can adapt to how this learner
    # actually studies — not just their scores.
    from . import behavior as behavior_store

    insights = user_memory.get("learner_insights")
    if isinstance(insights, dict) and insights.get("summary"):
        memory["learner_insights"] = insights
    engagement = await behavior_store.get_engagement(session)
    docs_by_dwell = sorted(
        (engagement.get("docs") or {}).items(),
        key=lambda kv: kv[1].get("dwell_secs", 0),
        reverse=True,
    )[:5]
    if engagement.get("actions_count"):
        memory["engagement"] = {
            "total_dwell_secs": round(engagement.get("total_dwell_secs", 0)),
            "top_docs": [
                {"doc_id": d, "dwell_secs": round(v.get("dwell_secs", 0))}
                for d, v in docs_by_dwell
                if v.get("dwell_secs", 0) > 0
            ],
            "tab_switches": engagement.get("tab_switches"),
        }
    patterns = await behavior_store.get_study_patterns(session)
    memory["study_patterns"] = {
        "best_study_hour_utc": patterns.get("best_study_hour"),
        "avg_quiz_duration_secs": patterns.get("avg_quiz_duration_secs"),
        "sessions": patterns.get("sessions"),
    }

    # Fatigue: derived from the recommendation session store — a learner 50
    # minutes into a session should get gentler content than a fresh one.
    session_data = user_memory.get("session")
    if isinstance(session_data, dict):
        fatigue = _derive_fatigue(session_data)
        if fatigue:
            memory["fatigue"] = fatigue

    _trace(state, f"memory: doc keys={list(doc_memory)} user keys={list(user_memory)}")
    return {"memory": memory}


# --- Node 4: generate ---


async def generate(state: AgentState) -> dict[str, Any]:
    """Dispatch to the feature-specific generation tool."""
    task_type = state["task_type"]
    common = (
        state["document_text"],
        state.get("analysis", {}),
        state.get("plan", {}),
        state.get("memory", {}),
    )
    if task_type == "notes":
        output = await tools.generate_notes(*common)
    elif task_type == "quiz":
        output = await tools.generate_quiz(*common)
    elif task_type == "flashcards":
        output = await tools.generate_flashcards(*common)
    else:  # pragma: no cover — task_type is validated upstream
        raise ValueError(f"Unknown task_type: {task_type}")

    _trace(state, f"generate: produced {task_type} ({output_summary(task_type, output)})")
    return {"output": output}


# --- Node 5: validate ---


async def validate(state: AgentState) -> dict[str, Any]:
    """Structural validation of the generated content (cheap, no LLM call)."""
    task_type = state["task_type"]
    output = state.get("output", {})
    problems: list[str] = []

    if task_type == "notes":
        md = output.get("markdown", "")
        if len(md.strip()) < 80:
            problems.append("notes markdown too short")
    elif task_type == "quiz":
        qs = output.get("questions", [])
        if len(qs) < 3:
            problems.append(f"only {len(qs)} questions (need >=3)")
        for q in qs:
            opts = q.get("options", [])
            if len(opts) != 4:
                problems.append(f"question {q.get('id')} has {len(opts)} options")
            if not (0 <= int(q.get("answer_idx", -1)) < len(opts)):
                problems.append(f"question {q.get('id')} has bad answer_idx")
    elif task_type == "flashcards":
        cards = output.get("cards", [])
        if len(cards) < 3:
            problems.append(f"only {len(cards)} cards (need >=3)")
        for c in cards:
            if not c.get("front") or not c.get("back"):
                problems.append(f"card {c.get('id')} missing front/back")

    ok = not problems
    _trace(state, f"validate: {'PASS' if ok else 'FAIL ' + '; '.join(problems)}")
    return {"validation": {"ok": ok, "problems": problems}}


# --- Node 6: finalize ---


async def finalize(state: AgentState) -> dict[str, Any]:
    """Persist the ContentItem and write back learnings to memory."""
    if not state.get("validation", {}).get("ok"):
        # Validation failed — surface the problems rather than persisting junk.
        return {
            "error": "Validation failed: "
            + "; ".join(state["validation"].get("problems", []))
        }

    session: AsyncSession = state["session"]
    doc_id = state["document_id"]
    task_type = state["task_type"]

    content_id = tools.new_content_id()
    item = ContentItem(
        user_id=user_ref_id(),
        id=content_id,
        document_id=doc_id,
        type=task_type,
        content=state["output"],
    )
    session.add(item)
    await session.flush()

    # Record this generation in per-doc memory so future runs know what exists.
    prior = (
        await memory_store.read_memory(session, "doc", doc_id, "prior_generations")
    ) or []
    prior.append({"type": task_type, "content_id": content_id})
    await memory_store.write_memory(
        session, "doc", doc_id, "prior_generations", prior
    )

    _trace(state, f"finalize: persisted {task_type} content_id={content_id}")
    return {
        "content_item": {
            "id": content_id,
            "document_id": doc_id,
            "type": task_type,
            "content": state["output"],
        },
        "error": None,
    }


# --- Helpers ---


def _derive_fatigue(session_data: dict) -> str | None:
    """Bucket the learner's current session length.

    Mirrors recommend/context.py's buckets: <20 min fresh, <50 focused,
    beyond that fatigued. Returns None when there's no recent activity
    (the session store expires after 2h of inactivity).
    """
    from datetime import datetime, timezone

    actions = session_data.get("actions") or []
    started_at = session_data.get("started_at")
    if not actions or not started_at:
        return None
    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        last = datetime.fromisoformat(str(actions[-1]["ts"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    mins = (last - start).total_seconds() / 60
    if mins < 0:
        return None
    if mins < 20:
        return "fresh"
    if mins < 50:
        return "focused"
    return "fatigued"


def json_summary(obj: Any) -> str:
    import json

    s = json.dumps(obj)
    return s if len(s) < 140 else s[:137] + "..."


def output_summary(task_type: str, output: dict[str, Any]) -> str:
    if task_type == "notes":
        return f"{len(output.get('markdown', ''))} chars"
    if task_type == "quiz":
        return f"{len(output.get('questions', []))} questions"
    if task_type == "flashcards":
        return f"{len(output.get('cards', []))} cards"
    return "?"
