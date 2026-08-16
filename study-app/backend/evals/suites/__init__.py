"""Shared plumbing for the eval suites.

Suites are pytest files marked `@pytest.mark.evals`. They call the REAL
production functions and record every (case, metric) observation into the
report — a red suite means a metric crossed its threshold.
"""

from __future__ import annotations

import json

import pytest

from evals.config import DATA_DIR

# The generation chain is expensive (3 LLM calls/case); several metric tests
# grade the SAME output, so results are cached per case for the session.
chain_cache: dict[str, tuple] = {}


def load_cases(name: str) -> list[dict]:
    """Load a prepared dataset (see evals.data). Fails with a pointer to the
    prepare task if missing."""
    path = DATA_DIR / f"{name}.jsonl"
    if not path.exists():
        pytest.fail(
            f"{path} not found — run `task study-app:evals-prepare` "
            "(or `uv run python -m evals.data {name}`)"
        )
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def case_ids(case: dict) -> str:
    """pytest `ids=` callable — one parametrized case → its dataset id."""
    return case["id"]


# --- Seeded learner memories for the personalization axis --------------------
# These mirror what retrieve_memory composes in production; the suites feed
# them straight into the generation tools, exactly as the graph does.

NOVICE_MEMORY = {
    "learner_profile": {
        "learner_level": "beginner",
        "stats": {
            "avg_score": 0.30,
            "score_history": [0.25, 0.30, 0.28, 0.35],
            "flashcard_known_ratio": 0.3,
        },
        "preferred_difficulty": "easy",
    }
}

ADVANCED_MEMORY = {
    "learner_profile": {
        "learner_level": "advanced",
        "stats": {
            "avg_score": 0.90,
            "score_history": [0.80, 0.85, 0.90, 0.92],
            "flashcard_known_ratio": 0.95,
        },
        "preferred_difficulty": "hard",
    }
}


# --- Real production generation chains ---------------------------------------


async def quiz_chain(case_id: str, document_text: str, memory: dict) -> tuple:
    """analyze → plan → generate_quiz, the same three LLM calls the LangGraph
    pipeline makes. Cached per case_id (namespaced — quiz and flashcard
    chains share the cache dict and must never see each other's results)."""
    from app.agent import tools

    key = f"quiz:{case_id}"
    if key not in chain_cache:
        analysis = await tools.analyze_document(document_text)
        plan = await tools.plan_task("quiz", analysis, memory, None)
        quiz = await tools.generate_quiz(document_text, analysis, plan, memory)
        chain_cache[key] = (analysis, plan, quiz)
    return chain_cache[key]


async def flashcard_chain(case_id: str, document_text: str, memory: dict) -> tuple:
    from app.agent import tools

    key = f"flashcards:{case_id}"
    if key not in chain_cache:
        analysis = await tools.analyze_document(document_text)
        plan = await tools.plan_task("flashcards", analysis, memory, None)
        deck = await tools.generate_flashcards(document_text, analysis, plan, memory)
        if not deck.get("cards"):
            # Transient empty generation — one retry before recording the
            # failure (the prod validate node would reject an empty deck).
            deck = await tools.generate_flashcards(
                document_text, analysis, plan, memory
            )
        chain_cache[key] = (analysis, plan, deck)
    return chain_cache[key]


def clamp01(score: float | None) -> float:
    """LLM judges occasionally return out-of-range numbers — clamp."""
    return max(0.0, min(1.0, float(score or 0.0)))


# --- Deterministic structural checks (mirror the validate node) --------------


def quiz_structural_ok(quiz: dict) -> tuple[bool, str]:
    """The same rules the pipeline's validate node enforces before persist."""
    questions = quiz.get("questions") or []
    if len(questions) < 3:
        return False, f"only {len(questions)} questions"
    for i, q in enumerate(questions):
        options = q.get("options") or []
        if len(options) != 4:
            return False, f"q{i} has {len(options)} options"
        try:
            idx = int(q.get("answer_idx", -1))
        except (TypeError, ValueError):
            return False, f"q{i} bad answer_idx"
        if not 0 <= idx < len(options):
            return False, f"q{i} answer_idx out of range"
        if not str(q.get("prompt", "")).strip():
            return False, f"q{i} empty prompt"
    return True, ""


def deck_structural_ok(deck: dict) -> tuple[bool, str]:
    cards = deck.get("cards") or []
    if len(cards) < 3:
        return False, f"only {len(cards)} cards"
    for i, c in enumerate(cards):
        variants = c.get("variants") or []
        if len(variants) < 2:
            return False, f"card {i} has {len(variants)} variants"
        for v in variants:
            if not str(v.get("front", "")).strip() or not str(v.get("back", "")).strip():
                return False, f"card {i} has empty front/back"
    return True, ""
