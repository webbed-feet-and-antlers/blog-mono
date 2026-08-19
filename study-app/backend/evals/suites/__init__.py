"""Shared plumbing for the eval suites.

Suites are pytest files marked `@pytest.mark.evals`. They call the REAL
production functions and record every (case, metric) observation into the
report — a red suite means a metric crossed its threshold.

Per-case work runs CONCURRENTLY (bounded by EVALS_CONCURRENCY): the
generation tools are pure async functions with no DB, and the OpenAI
client pools connections — the sequential loops were the only thing
making a full run cost an evening.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from evals.config import DATA_DIR, EVALS_CONCURRENCY

# The generation chain is expensive (3 LLM calls/case); several metric tests
# grade the SAME output, so results are cached per case for the session.
chain_cache: dict[str, tuple] = {}

# analyze_document is memory-independent — one analysis per passage serves
# every chain kind (quiz/flashcards/notes) and every memory variant.
_analysis_cache: dict[str, dict] = {}


async def analysis_for(document_text: str) -> dict:
    """analyze_document memoized by text hash, shared across all chains."""
    from app.agent import tools

    key = hashlib.sha1(document_text.encode()).hexdigest()[:16]
    if key not in _analysis_cache:
        _analysis_cache[key] = await tools.analyze_document(document_text)
    return _analysis_cache[key]


async def gather_bounded(factories, limit: int | None = None):
    """Run coroutine factories concurrently under EVALS_CONCURRENCY.

    Factories (not coroutines) so creation happens inside the semaphore —
    a thousand pending create() calls would otherwise all start together.
    Returns results in input order; exceptions propagate like gather."""
    sem = asyncio.Semaphore(limit or EVALS_CONCURRENCY)

    async def run(factory):
        async with sem:
            return await factory()

    return await asyncio.gather(*(run(f) for f in factories))


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
    chains share the cache dict and must never see each other's results).
    The analysis is shared across chains via analysis_for()."""
    from app.agent import tools

    key = f"quiz:{case_id}"
    if key not in chain_cache:
        analysis = await analysis_for(document_text)
        quiz = None
        for _ in range(3):  # transient JSON failures from the generator
            try:
                plan = await tools.plan_task("quiz", analysis, memory, None)
                quiz = await tools.generate_quiz(document_text, analysis, plan, memory)
                break
            except ValueError:
                continue
        if quiz is None:
            raise RuntimeError(f"quiz chain failed for {case_id} after 3 attempts")
        chain_cache[key] = (analysis, plan, quiz)
    return chain_cache[key]


async def flashcard_chain(case_id: str, document_text: str, memory: dict) -> tuple:
    from app.agent import tools

    key = f"flashcards:{case_id}"
    if key not in chain_cache:
        analysis = await analysis_for(document_text)
        deck = None
        for _ in range(3):
            try:
                plan = await tools.plan_task("flashcards", analysis, memory, None)
                deck = await tools.generate_flashcards(document_text, analysis, plan, memory)
                if deck.get("cards"):
                    break
                # Transient empty generation — retry (the prod validate node
                # would reject an empty deck).
                deck = None
            except ValueError:
                continue
        if deck is None:
            raise RuntimeError(f"flashcard chain failed for {case_id} after 3 attempts")
        chain_cache[key] = (analysis, plan, deck)
    return chain_cache[key]


def clamp01(score: float | None) -> float:
    """LLM judges occasionally return out-of-range numbers — clamp."""
    return max(0.0, min(1.0, float(score or 0.0)))


async def judge_score(metric, test_case) -> tuple[float | None, str]:
    """a_measure with one retry on unparseable verdicts.

    GEval signals "could not extract a verdict" with a negative score —
    that is a harness hiccup, not a judgment. Re-ask once, then give up
    (None) so callers skip the case instead of counting it as a zero.
    NOTE: criteria must not mention a 0.0-1.0 scale — GEval's prompt has
    the judge score 0-10 and normalizes by /10, so conflicting scales
    produce crushed or anchored-to-example-zero scores."""
    reason = ""
    for _ in range(2):
        await metric.a_measure(test_case, _show_indicator=False)
        reason = metric.reason or ""
        if metric.score is not None and metric.score >= 0:
            return clamp01(metric.score), reason
    return None, reason


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
